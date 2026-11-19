"""Static index: the suffix array of one search mode, cut into blocks that a
browser reads with HTTP range requests (S3, a CDN, any static host), with no
server. The reader is web/src/lib/static-index.js.

Files (integers are little-endian):

    manifest.json     sizes and file names
    root.bin          the root node
    L<k>-<part>.bin   the nodes of level k, one after the other; level 0 holds the leaves
    text-<part>.bin   the text, 1 byte per note: to check a key and show the notes of a match
    dup-<part>.bin    dup[r] for each rank r, to count the files of a large match
    files-<k>.json    names and titles of the files with notes in positions
                      [k * files_chunk_notes, (k + 1) * files_chunk_notes)

A part is at most `part_bytes` and MAX_PART long and a node is never split
between two parts, so that hosts with a file size limit can serve the index.
The default is parts of MAX_PART (4 GiB: the offsets in a node are 32-bit),
so 1 part per level for a shard: on S3, a range GET is about 32 ms in a
region of an object that was read a short time before, and about 72 ms
elsewhere (bench/README.md), so all searches should read few objects.

Leaf: `leaf` consecutive ranks of the suffix array.

    u32 n
    u32 pos[n]    start of the suffix in the text
    u8  c[n]      notes that the key has in common with the key before it (0 for the first key of the leaf)
    u8  len[n]    notes stored after them
    u8  dup[n]
    u8  notes[sum(len)]

key[r] is the first d[r] notes of the suffix at rank r, with
d[r] = 1 + max(LCP(r - 1, r), LCP(r, r + 1)), at least key_min, at most CAP
and the length of the suffix. So a query of m <= CAP notes compares
completely with every key except at most one in the whole array: a key that
is a proper prefix of the query (its suffix has no neighbour that shares d[r]
notes with it). The reader reads the text of that suffix (1 range request).

dup[r] = min(CAP, LCP(r, r')) where r' is the next rank of a suffix of the
same file (0 if there is none). In the range [lo, hi) of the suffixes that
start with a query of m notes, the ranks of one file follow each other with
an LCP of at least m, so

    files = (hi - lo) - #{r in [lo, hi) : dup[r] >= m}

Internal node: up to `fanout` children of the level below.

    u32 n
    u32 first     index of the first child in its level
    u32 pos[n]    position of the first suffix of each child (to check its key)
    u32 part[n], off[n], size[n]   where each child is
    u8  c[n], len[n], notes[...]   the key of the first suffix of each child, front coded

The root is an internal node over the top level, in root.bin.
"""

import json
import resource
import sys
import time
from pathlib import Path

import numpy as np

FORMAT = "what-the-midi-static"
VERSION = 1
CAP = 64  # = index.MAX_QUERY_NOTES: longest query and longest key
CHUNK = 1 << 24  # pairs compared at a time
GROUP = 1 << 27  # notes of the files whose dup pairs are sorted at a time
MAX_PART = 1 << 32  # bytes of a part, at most: the offsets in a node are 32-bit


def padded_text(n: int) -> np.ndarray:
    """A buffer for a text of n notes, with CAP + 8 zeros after it for words()."""
    return np.zeros(n + CAP + 8, dtype=np.uint8)


def words(buf: np.ndarray, n: int) -> np.ndarray:
    """W[p] = buf[p : p + 8] as a big-endian u64, for p < n + CAP, where buf
    comes from padded_text(n). An unaligned view of buf with no copy: an
    array would add 8 bytes per note, for gathers 3 times faster."""
    assert len(buf) >= n + CAP + 8 and not buf[n:].any()
    return np.ndarray((n + CAP,), dtype=">u8", buffer=buf, strides=(1,))


def capped_lcp(w: np.ndarray, n: int, a: np.ndarray, b: np.ndarray, cap: int = CAP) -> np.ndarray:
    """min(cap, LCP of the suffixes that start at a and b), element by element."""
    out = np.empty(len(a), dtype=np.uint8)
    for s in range(0, len(a), CHUNK):
        aa = np.asarray(a[s : s + CHUNK], dtype=np.int64)
        bb = np.asarray(b[s : s + CHUNK], dtype=np.int64)
        res = np.full(len(aa), cap, dtype=np.int64)
        idx = np.arange(len(aa))
        x_a, x_b = aa, bb
        for t in range(0, cap, 8):
            x = w[x_a + t] ^ w[x_b + t]
            hit = x != 0
            xh = x[hit]
            zero_bytes = np.zeros(len(xh), dtype=np.int64)  # leading zero bytes of xh
            for k in range(1, 8):
                zero_bytes += xh < np.uint64(1 << (8 * k))
            res[idx[hit]] = np.minimum(cap, t + zero_bytes)
            keep = ~hit
            idx, x_a, x_b = idx[keep], x_a[keep], x_b[keep]
            if not len(idx):
                break
        # the zeros after the end are not notes
        res = np.minimum(res, n - np.maximum(aa, bb))
        out[s : s + CHUNK] = res
    return out


class PartWriter:
    """Writes nodes one after the other in files of at most part_bytes."""

    def __init__(self, out: Path, name: str, part_bytes: int):
        self.out, self.name, self.part_bytes = out, name, part_bytes
        self.parts: list[str] = []
        self.file = None
        self.size = 0

    def write(self, data: bytes) -> tuple[int, int, int]:
        if self.file is None or (self.size and self.size + len(data) > self.part_bytes):
            self._next()
        offset = self.size
        self.file.write(data)
        self.size += len(data)
        return len(self.parts) - 1, offset, len(data)

    def _next(self):
        if self.file:
            self.file.close()
        self.parts.append(f"{self.name}-{len(self.parts):05d}.bin")
        self.file = open(self.out / self.parts[-1], "wb")
        self.size = 0

    def close(self) -> list[str]:
        if self.file:
            self.file.close()
        return self.parts


def write_array(out: Path, name: str, data: np.ndarray, part_bytes: int) -> list[str]:
    parts = []
    for k, s in enumerate(range(0, max(1, len(data)), part_bytes)):
        parts.append(f"{name}-{k:05d}.bin")
        with open(out / parts[-1], "wb") as f:
            f.write(memoryview(np.ascontiguousarray(data[s : s + part_bytes])))
    return parts


def front_coded(keys: list[bytes]) -> tuple[np.ndarray, np.ndarray, bytes]:
    c = np.zeros(len(keys), dtype=np.uint8)
    for j in range(1, len(keys)):
        a, b = keys[j - 1], keys[j]
        k, m = 0, min(len(a), len(b))
        while k < m and a[k] == b[k]:
            k += 1
        c[j] = k
    lens = np.array([len(k) for k in keys], dtype=np.uint8) - c
    return c, lens, b"".join(k[ci:] for k, ci in zip(keys, c.tolist()))


def internal_node(first: int, children: list[tuple], text: np.ndarray, sa, d) -> bytes:
    """children: (part, offset, size, first rank) of each child."""
    ranks = [ch[3] for ch in children]
    pos = np.asarray(sa[ranks], dtype=np.int64)
    keys = [text[p : p + int(d[r])].tobytes() for p, r in zip(pos.tolist(), ranks)]
    c, lens, notes = front_coded(keys)
    loc = np.array([ch[:3] for ch in children], dtype=np.uint32).T
    return b"".join([
        np.array([len(children), first], dtype="<u4").tobytes(),
        pos.astype("<u4").tobytes(),
        loc.astype("<u4").tobytes(),
        c.tobytes(), lens.tobytes(), notes,
    ])


def write_files(out, doc_starts, names, titles=None, chunk_notes=1 << 19):
    """files-<k>.json: the start in the text, the name and the title of the
    files that have notes in positions [k * chunk_notes, (k + 1) *
    chunk_notes). The reader finds the chunk of a position with no table, so
    the manifest stays small for millions of files."""
    out = Path(out)
    manifest = json.loads((out / "manifest.json").read_text())
    n = int(doc_starts[-1])
    for k, a in enumerate(range(0, n, chunk_notes)):
        s = int(np.searchsorted(doc_starts, a, side="right")) - 1
        e = int(np.searchsorted(doc_starts, min(n, a + chunk_notes), side="left"))
        body = {"first": s, "starts": np.asarray(doc_starts[s : e + 1]).tolist(), "names": list(names[s:e])}
        if titles is not None:
            body["titles"] = list(titles[s:e])
        (out / f"files-{k:05d}.json").write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")))
    manifest["files_chunk_notes"] = chunk_notes
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


def part_dirs(src) -> list[Path]:
    """The parts of an index with several parts (part-000/, part-001/…), or []."""
    return sorted(Path(src).glob("part-*"))


def load_source(src, mode="exact"):
    """(buf, sa, doc_starts) of src: an index part, the shard of
    bench/fts5.py, or an index with several parts. buf is the text in a
    buffer of padded_text(). The parts go one after the other in one text,
    and pydivsufsort builds its suffix array again (about 2 min for 1.74 G
    notes): one static index for the whole collection, so a search reads
    one tree."""
    src = Path(src)
    dirs = part_dirs(src) or [src]
    texts = [np.load(d / f"text_{mode}.npy", mmap_mode="r") for d in dirs]
    starts = [np.load(d / "doc_starts.npy") for d in dirs]
    offsets = np.cumsum([0] + [len(t) for t in texts])
    n = int(offsets[-1])
    buf = padded_text(n)
    for t, o in zip(texts, offsets):
        buf[o : o + len(t)] = t
    if len(dirs) == 1:
        return buf, np.load(src / f"sa_{mode}.npy"), starts[0]
    if n >= 2**31 - 1:
        raise ValueError("more than 2**31 notes: too many for one suffix array of 32-bit integers")
    from pydivsufsort import divsufsort

    doc_starts = np.concatenate([s[:-1] + o for s, o in zip(starts, offsets)] + [[n]]).astype(np.int64)
    return buf, divsufsort(buf[:n]).astype(np.int32, copy=False), doc_starts


def load_names(src, names_from=None):
    """(names, titles) of the files of src, from names.json and titles.json
    in names_from (default: src). For the shard of bench/fts5.py, names_from
    is the index part it comes from, and docs.npy gives the file numbers.
    For an index with several parts: the files of all the parts."""
    src = Path(src)
    if not names_from and part_dirs(src):
        names, titles = [], []
        for d in part_dirs(src):
            n, t = load_names(d)
            if n is None:
                return None, None
            names += n
            titles = None if titles is None or t is None else titles + t
        return names, titles
    origin = Path(names_from) if names_from else src
    if not (origin / "names.json").exists():
        return None, None
    names = json.loads((origin / "names.json").read_text())
    titles = json.loads((origin / "titles.json").read_text()) if (origin / "titles.json").exists() else None
    if names_from and (src / "docs.npy").exists():
        docs = np.load(src / "docs.npy").tolist()
        names = [names[d] for d in docs]
        titles = titles and [titles[d] for d in docs]
    return names, titles


def build(src, out, mode="exact", leaf=4096, fanout=256, root_max=None, key_min=0,
          part_bytes=MAX_PART, names=None, titles=None, files_chunk_notes=1 << 19, progress=True):
    """Writes the static index of one mode of the index in src to out.

    src holds text_<mode>.npy, sa_<mode>.npy and doc_starts.npy (an index
    part, or the shard of bench/fts5.py), or is an index with several parts
    (see load_source). The root holds at most root_max
    children (default: fanout); levels are added until it does. names and
    titles (one per file) go in files-<k>.json.
    """
    src, out = Path(src), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    root_max = root_max or fanout
    part_bytes = min(part_bytes, MAX_PART)
    log = print if progress else (lambda *a, **k: None)
    started = time.time()
    times = {}

    def step(name):
        now = time.time()
        times[name] = round(now - step.t, 1)
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
        log(f"  {name}: {times[name]} s, peak RAM {peak / 1e9:.1f} GB", flush=True)
        step.t = now

    step.t = started

    # Memory, for n notes: text n, sa 4n (32-bit), lcp (then c), d and dup
    # n each, and the sort keys of one group of files: about 8n + 4.5 GB.
    buf, sa, doc_starts = load_source(src, mode)
    n = len(sa)
    if n >= 2**32:
        raise ValueError("more than 2**32 notes: split the index in parts")
    text, w = buf[:n], words(buf, n)
    step("load")

    # lcp[r] = min(CAP, LCP(r - 1, r)), lcp[0] = lcp[n] = 0
    lcp = np.zeros(n + 1, dtype=np.uint8)
    lcp[1:n] = capped_lcp(w, n, sa[:-1], sa[1:])
    d = np.maximum(lcp[:n], lcp[1:])
    d += 1
    np.clip(d, key_min, CAP, out=d)
    end = np.flatnonzero(sa >= n - CAP)  # suffixes of fewer than CAP notes
    d[end] = np.minimum(d[end], n - sa[end].astype(np.int64)).astype(np.uint8)
    del end
    step("keys")

    # next rank of the same file, for the files of one group of about GROUP
    # notes at a time: sort (file, rank) packed in one integer, 10 times
    # faster than a stable argsort of the files
    dup = np.zeros(n, dtype=np.uint8)
    cuts = np.unique(doc_starts[np.searchsorted(doc_starts, np.minimum(np.arange(0, n + GROUP, GROUP), n))])
    for p0, p1 in zip(cuts[:-1].tolist(), cuts[1:].tolist()):
        ranks = np.concatenate([
            s + np.flatnonzero((sa[s : s + CHUNK] >= p0) & (sa[s : s + CHUNK] < p1))
            for s in range(0, n, CHUNK)
        ])
        key = np.searchsorted(doc_starts, sa[ranks], side="right")
        key -= 1
        key <<= 32
        key |= ranks
        del ranks
        key.sort()
        order = key & 0xFFFFFFFF
        key >>= 32
        same = key[1:] == key[:-1]
        del key
        first, after = order[:-1][same], order[1:][same]
        del order, same
        dup[first] = capped_lcp(w, n, sa[first], sa[after])
        del first, after
    del w
    step("dup")

    # leaves
    c = lcp[:n]
    del lcp
    c[::leaf] = 0
    assert (c <= d).all()
    lens = d - c
    writer = PartWriter(out, "L0", part_bytes)
    nodes = []  # (part, offset, size, first rank) of each node of the current level
    group = leaf * max(1, (1 << 22) // leaf)
    for g in range(0, n, group):
        e = min(n, g + group)
        ln = lens[g:e].astype(np.int64)
        begin = np.cumsum(ln) - ln
        idx = np.repeat(sa[g:e].astype(np.int64) + c[g:e] - begin, ln) + np.arange(int(ln.sum()))
        notes = text[idx]
        ends = np.r_[begin, ln.sum()]
        for s in range(g, e, leaf):
            t = min(e, s + leaf)
            k = t - s
            data = b"".join([
                np.array([k], dtype="<u4").tobytes(),
                sa[s:t].astype("<u4").tobytes(),
                c[s:t].tobytes(), lens[s:t].astype(np.uint8).tobytes(), dup[s:t].tobytes(),
                notes[ends[s - g] : ends[t - g]].tobytes(),
            ])
            nodes.append((*writer.write(data), s))
    levels = [{"nodes": len(nodes), "parts": writer.close()}]
    step("leaves")

    while len(nodes) > root_max:
        writer = PartWriter(out, f"L{len(levels)}", part_bytes)
        upper = []
        for s in range(0, len(nodes), fanout):
            data = internal_node(s, nodes[s : s + fanout], text, sa, d)
            upper.append((*writer.write(data), nodes[s][3]))
        levels.append({"nodes": len(upper), "parts": writer.close()})
        nodes = upper
    (out / "root.bin").write_bytes(internal_node(0, nodes, text, sa, d))
    step("internal nodes")

    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "mode": mode,
        "cap": CAP,
        "notes": n,
        "files": len(doc_starts) - 1,
        "leaf": leaf,
        "fanout": fanout,
        "key_min": key_min,
        "levels": levels,
        "text": {"part_bytes": part_bytes, "parts": write_array(out, "text", text, part_bytes)},
        "dup": {"part_bytes": part_bytes, "parts": write_array(out, "dup", dup, part_bytes)},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    step("text and dup")

    if names is not None:
        write_files(out, doc_starts, names, titles, files_chunk_notes)

    sizes = {f.name: f.stat().st_size for f in out.iterdir()}
    stats = {
        "notes": n,
        "levels": len(levels),
        "root_bytes": sizes["root.bin"],
        "leaf_bytes": sum(v for k, v in sizes.items() if k.startswith("L0-")),
        "internal_bytes": sum(v for k, v in sizes.items() if k.startswith("L") and not k.startswith("L0-")),
        "total_bytes": sum(sizes.values()),
        "key_notes_per_rank": round(float(lens.mean()), 2),
        "seconds": round(time.time() - started, 1),
        "peak_ram_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024),
        "steps": times,
    }
    (out / "build.json").write_text(json.dumps(stats, indent=1))
    return stats


def serve(out_dir, host="127.0.0.1", port=8766):
    """Serves a static index like a public bucket: range requests, and CORS
    for a page on another origin (npm run dev)."""
    from aiohttp import web

    @web.middleware
    async def cors(request, handler):
        if request.method == "OPTIONS":
            response = web.Response()
        else:
            response = await handler(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Range"
        response.headers["Access-Control-Expose-Headers"] = "Content-Range, Content-Length"
        return response

    app = web.Application(middlewares=[cors])
    app.router.add_static("/", Path(out_dir))
    web.run_app(app, host=host, port=port)
