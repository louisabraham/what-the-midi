"""Suffix array against SQLite FTS5 (4-grams of pitches), local and on
sqlite4all.reduck.ai.

Each melody of an index becomes one FTS5 row: a list of tokens, one for
each 4 consecutive pitches (8 hex digits: "3c3e4043" is C4 D4 E4 G4).
A query of n >= 4 notes is the phrase of its n - 3 tokens: the phrase
matches only where the tokens are consecutive, so it finds the exact
sequence of n notes, like the suffix array.

    uv run bench/fts5.py local data/db-lakh data/fts5-lakh.sqlite
    API_KEY=rsk_... uv run bench/fts5.py upload data/db-lakh midi-lakh
    API_KEY=rsk_... uv run bench/fts5.py bench data/db-lakh data/fts5-lakh.sqlite midi-lakh -o data/bench/fts5-lakh.json
    uv run bench/fts5.py bench data/db-all data/fts5-all.sqlite -o data/bench/fts5-all.json

Only the exact mode (no transposition, octave kept).
"""

import argparse
import http.client
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from pydivsufsort import divsufsort, sa_search

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.index import open_database  # noqa: E402
from whatthemidi.notes import parse_query  # noqa: E402

HOST = "sqlite4all.reduck.ai"
SCHEMA = "CREATE VIRTUAL TABLE m USING fts5(grams, content='', columnsize=0)"
MAX_BODY = 250_000  # bytes; the API accepts 256 KiB
# JSON of one INSERT statement of a batch, without the rowid and the tokens
ROW_JSON = len(json.dumps({"sql": "INSERT INTO m (rowid, grams) VALUES (?, ?)", "params": [
    {"type": "integer", "value": ""}, {"type": "text", "value": ""}]})) + 2


# ---------------------------------------------------------------- tokens


HEX = np.frombuffer(b"0123456789abcdef", dtype=np.uint8)


def tokens(pitches):
    """'3c3e4043 3e404345 ...': one token per 4 consecutive pitches."""
    p = np.asarray(pitches, dtype=np.uint8)
    n = len(p) - 3
    if n <= 0:
        return ""
    out = np.empty((n, 9), dtype=np.uint8)
    for j in range(4):
        out[:, 2 * j] = HEX[p[j : j + n] >> 4]
        out[:, 2 * j + 1] = HEX[p[j : j + n] & 15]
    out[:, 8] = ord(" ")
    return out.tobytes()[:-1].decode()


def phrase(pitches):
    return f'"{tokens(pitches)}"'


def parts(db_dir):
    """Directories of the parts of an index, with their first doc number."""
    db_dir = Path(db_dir)
    dirs = sorted(db_dir.glob("part-*")) or [db_dir]
    first = 0
    for d in dirs:
        yield d, first
        first += len(np.load(d / "doc_starts.npy")) - 1


def melodies(db_dir):
    """(doc, pitches) of each melody of an index."""
    for part, first in parts(db_dir):
        text = np.load(part / "text_exact.npy", mmap_mode="r")
        starts = np.load(part / "doc_starts.npy")
        for d in range(len(starts) - 1):
            yield first + d, np.asarray(text[starts[d] : starts[d + 1] - 1])


# ---------------------------------------------------------------- sqlite4all


class Remote:
    def __init__(self, name):
        self.name, self.key = name, os.environ["API_KEY"]
        self.conn = http.client.HTTPSConnection(HOST, timeout=60)

    def call(self, method, path, body=None):
        """(status, JSON answer, server SQL time in µs); retries on 429/503.
        self.last_ms: time of the last attempt (without the waits)."""
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(100):
            t = time.perf_counter()
            try:
                self.conn.request(method, path, body=data, headers={
                    "Authorization": f"Bearer {self.key}", "Content-Type": "application/json"})
                r = self.conn.getresponse()
                answer = r.read()
            except (http.client.HTTPException, OSError):
                self.conn.close()
                self.conn = http.client.HTTPSConnection(HOST, timeout=60)
                time.sleep(1)
                continue
            self.last_ms = (time.perf_counter() - t) * 1000
            if r.status in (429, 503):
                time.sleep(float(r.getheader("Retry-After") or 1 + attempt % 10))
                continue
            sql_us = int(r.getheader("X-Sqlite-Time-Us") or 0)
            return r.status, (json.loads(answer) if answer else None), sql_us
        raise RuntimeError(f"{method} {path}: too many retries")

    def exec(self, sql, params=(), timeout_ms=5000):
        status, answer, sql_us = self.call("POST", f"/v1/databases/{self.name}/exec", {
            "sql": sql, "params": list(params), "timeout_ms": timeout_ms})
        if status != 200:
            raise RemoteError(answer)
        return answer, sql_us

    def batch(self, statements):
        timeouts = 0
        for _ in range(100):
            status, answer, sql_us = self.call("POST", f"/v1/databases/{self.name}/batch",
                                               {"statements": statements, "timeout_ms": 5000})
            if status == 200:
                return sql_us
            code = RemoteError(answer).code
            if code == "QUERY_TIMEOUT" and timeouts < 3:  # nothing changed: send it again
                timeouts += 1
            elif code == "LOCAL_STORAGE_LIMIT":
                # the server copies the writes to S3 before it takes more
                time.sleep(15)
            else:
                break
        raise RemoteError(answer)


class RemoteError(RuntimeError):
    @property
    def code(self):
        try:
            return self.args[0]["error"]["code"]
        except (KeyError, TypeError, IndexError):
            return None


def text(value):
    return {"type": "text", "value": value}


def integer(value):
    return {"type": "integer", "value": str(value)}


# ---------------------------------------------------------------- commands


def build_local(db_dir, out):
    out = Path(out)
    out.unlink(missing_ok=True)
    con = sqlite3.connect(out)
    con.execute(SCHEMA)
    started = time.time()
    n = 0
    with con:
        for d, p in melodies(db_dir):
            con.execute("INSERT INTO m (rowid, grams) VALUES (?, ?)", (d, tokens(p)))
            n += max(0, len(p) - 3)
    inserted = time.time()
    con.execute("INSERT INTO m (m) VALUES ('optimize')")
    con.commit()
    con.close()
    print(json.dumps({"tokens": n, "insert_s": round(inserted - started, 1),
                      "optimize_s": round(time.time() - inserted, 1),
                      "mb": round(out.stat().st_size / 1e6, 1)}))


def batches(items):
    """Lists of (doc, tokens) of whole melodies, each list under MAX_BODY
    bytes of JSON (one request)."""
    current, size = [], 0
    for d, p in items:
        t = tokens(p)
        n = len(t) + len(str(d)) + ROW_JSON
        if current and size + n > MAX_BODY:
            yield current
            current, size = [], 0
        current.append((d, t))
        size += n
    if current:
        yield current


def uploaded(name, all_batches, jobs):
    """Numbers of the batches already in the remote database. Each batch
    writes its number to the table uploaded; a batch of a database without
    this table is found with its first melody (a contentless FTS5 table
    cannot be scanned)."""
    remote = Remote(name)
    remote.exec("CREATE TABLE IF NOT EXISTS uploaded (batch INTEGER PRIMARY KEY)")
    done = {int(r[0]["value"]) for r in remote.exec("SELECT batch FROM uploaded")[0]["rows"]}
    if done:
        return done
    local = threading.local()

    def present(k):
        if not hasattr(local, "remote"):
            local.remote = Remote(name)
        d, t = all_batches[k][0]
        answer, _ = local.remote.exec("SELECT count(*) FROM m WHERE m MATCH ? AND rowid = ?",
                                      [text(f'"{t[:8]}"'), integer(d)])
        return int(answer["rows"][0][0]["value"]) > 0

    with ThreadPoolExecutor(jobs) as pool:
        found = list(pool.map(present, range(len(all_batches))))
    done = {k for k, f in enumerate(found) if f}
    for i in range(0, len(done), 400):  # 400 parameters per statement (limit: 500)
        part = sorted(done)[i : i + 400]
        remote.exec(f"INSERT INTO uploaded (batch) VALUES {', '.join('(?)' for _ in part)}",
                    [integer(k) for k in part])
    return done


def upload(db_dir, name, jobs=4, optimize=True):
    first = Remote(name)
    status, answer, _ = first.call("POST", "/v1/databases", {"name": name})
    if status not in (201, 409):
        raise RuntimeError(answer)
    if status == 201:
        first.exec(SCHEMA)
        first.exec("CREATE TABLE uploaded (batch INTEGER PRIMARY KEY)")
    all_batches = list(batches(melodies(db_dir)))
    done = uploaded(name, all_batches, jobs) if status == 409 else set()
    todo = [k for k in range(len(all_batches)) if k not in done]
    print(f"{len(todo)} batches to send, {len(done)} already uploaded", flush=True)

    local = threading.local()
    started, sql_us, sent = time.time(), 0, 0

    def send(k):
        if not hasattr(local, "remote"):
            local.remote = Remote(name)
        return local.remote.batch([
            {"sql": "INSERT INTO m (rowid, grams) VALUES (?, ?)", "params": [integer(d), text(t)]}
            for d, t in all_batches[k]
        ] + [{"sql": "INSERT INTO uploaded (batch) VALUES (?)", "params": [integer(k)]}])

    with ThreadPoolExecutor(jobs) as pool:
        try:
            for us in pool.map(send, todo):
                sql_us += us
                sent += 1
                if sent % 100 == 0 or sent == len(todo):
                    print(f"  {sent}/{len(todo)} batches, {time.time() - started:.0f} s, "
                          f"SQL {sql_us / 1e6:.1f} s", flush=True)
        except BaseException:
            pool.shutdown(wait=True, cancel_futures=True)  # the requests already sent end
            raise
    print(json.dumps({"upload_s": round(time.time() - started, 1), "sql_s": round(sql_us / 1e6, 1)}))
    if optimize:
        try:
            _, us = first.exec("INSERT INTO m (m) VALUES ('optimize')")
            print(f"optimize: SQL {us / 1e6:.1f} s")
        except RemoteError as e:
            print(f"optimize failed: {e}")
    print(json.dumps(first.call("GET", "/v1/databases")[1]))


# a melody with more notes does not fit in one request: shard leaves it out
MAX_ROW_NOTES = (MAX_BODY - ROW_JSON - 100) // 9 + 3


def shard(db_dir, out_dir, max_bytes):
    """The first melodies of an index, up to max_bytes of FTS5 index, in
    out_dir: fts5.sqlite (one transaction per upload batch, like upload,
    without optimize), and text_exact.npy, doc_starts.npy, sa_exact.npy
    (a suffix array of the same notes). Melodies longer than MAX_ROW_NOTES
    are left out. Row k is the k-th melody of the shard."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "fts5.sqlite"
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path, isolation_level=None)
    con.execute(SCHEMA)
    page = con.execute("PRAGMA page_size").fetchone()[0]
    kept, long = [], []  # doc numbers in the index

    def items():
        for d, p in melodies(db_dir):
            if len(p) > MAX_ROW_NOTES:
                long.append(d)
                continue
            kept.append(d)
            yield len(kept) - 1, p

    started, docs, n, size, k = time.time(), 0, 0, 0, 0
    for k, batch in enumerate(batches(items()), 1):
        con.execute("BEGIN")
        con.executemany("INSERT INTO m (rowid, grams) VALUES (?, ?)", batch)
        con.execute("COMMIT")
        docs = batch[-1][0] + 1
        n += sum((len(t) + 1) // 9 for _, t in batch)
        size = con.execute("PRAGMA page_count").fetchone()[0] * page
        if k % 1000 == 0:
            print(f"  {k} batches, {docs:,} melodies, {n:,} tokens, {size / 2**20:,.0f} MiB, "
                  f"{time.time() - started:.0f} s", flush=True)
        if size >= max_bytes:
            break
    con.close()

    kept = kept[:docs]
    part = next(parts(db_dir))[0]
    starts = np.load(part / "doc_starts.npy")
    assert kept[-1] < len(starts) - 1, "the shard must be in the first part"
    text = np.load(part / "text_exact.npy", mmap_mode="r")
    notes = np.concatenate([text[starts[d] : starts[d + 1]] for d in kept])  # with the separators
    np.save(out / "text_exact.npy", notes)
    np.save(out / "doc_starts.npy", np.r_[0, np.cumsum(starts[np.array(kept) + 1] - starts[kept])])
    np.save(out / "docs.npy", np.array(kept))
    t = time.time()
    np.save(out / "sa_exact.npy", divsufsort(notes))
    print(json.dumps({"batches": k, "melodies": docs, "left_out": len([d for d in long if d < kept[-1]]),
                      "notes": len(notes) - docs, "tokens": n, "fts5_bytes": size,
                      "fts5_s": round(t - started, 1), "suffix_array_s": round(time.time() - t, 1)}))


class Plain:
    """text, suffix array and doc starts of the exact mode (see shard)."""

    def __init__(self, path):
        path = Path(path)
        self.texts = {"exact": np.load(path / "text_exact.npy", mmap_mode="c")}
        self.sas = {"exact": np.load(path / "sa_exact.npy", mmap_mode="c")}
        self.doc_starts = np.load(path / "doc_starts.npy")


TUNES = [
    ("Für Elise", "E5 D#5 E5 D#5 E5 B4 D5 C5 A4"),
    ("Italian Concerto 3", "A#5 F5 E5 F5 A#5 F5 A5 F5 G5 F5 E5 F5 G5 F5 A5 F5 A#5 F5 E5 F5 A#5"),
    ("Italian Concerto 1", "F5 A4 A#4 C5 D5 C5 F5 A#4"),
    ("Scarlatti K513", "79,74,71,67,71,67,72,67,74,67,76,67"),
    ("Ode to Joy", "E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4"),
    ("Twinkle", "C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4"),
    ("Beethoven 5", "G4 G4 G4 D#4 F4 F4 F4 D4"),
    ("Jingle Bells", "E4 E4 E4 E4 E4 E4 E4 G4 C4 D4 E4"),
    ("Happy Birthday", "G4 G4 A4 G4 C5 B4 G4 G4 A4 G4 D5 C5"),
    ("C D E F", "C4 D4 E4 F4"),
]
LENGTHS = [4, 6, 8, 12, 16, 32]


def queries(db_dir, per_length=30, seed=0):
    """(label, pitches): the tunes, then random pieces of the melodies."""
    out = [(name, parse_query(q)) for name, q in TUNES]
    texts = [np.load(d / "text_exact.npy", mmap_mode="r") for d, _ in parts(db_dir)]
    sizes = np.array([len(t) for t in texts], dtype=np.float64)
    rng = np.random.default_rng(seed)
    for n in LENGTHS:
        k = 0
        while k < per_length:
            text = texts[rng.choice(len(texts), p=sizes / sizes.sum())]
            i = int(rng.integers(0, len(text) - n))
            piece = np.asarray(text[i : i + n])
            if (piece == 255).any():
                continue
            out.append((f"random {n}", piece.tolist()))
            k += 1
    return out


def timed(f, repeat=3):
    """(best time in ms, result)."""
    best, result = float("inf"), None
    for _ in range(repeat):
        t = time.perf_counter()
        result = f()
        best = min(best, time.perf_counter() - t)
    return best * 1000, result


COUNT = "SELECT count(*) FROM m WHERE m MATCH ?"
FIRST_10 = "SELECT rowid FROM m WHERE m MATCH ? LIMIT 10"


def bench(db_dir, local, name, per_length, out):
    """Times, for each query:
    sa_ms      suffix array: number of files with the sequence
    fts_ms     local FTS5: the same number (SELECT count(*))
    fts10_ms   local FTS5: the first 10 files (no ranking)
    app_ms     what-the-midi search: top 10 files ranked, with their notes
    remote_ms  sqlite4all: SELECT count(*), measured by the client
    remote_sql_ms  sqlite4all: SQLite time of that request (X-Sqlite-Time-Us)"""
    db = open_database(db_dir) if (Path(db_dir) / "meta.json").exists() else Plain(db_dir)
    dbs, offsets = getattr(db, "parts", [db]), getattr(db, "offsets", [0])
    con = sqlite3.connect(local)
    remote = Remote(name) if name else None

    def sa_count(q):
        n = 0
        for part, off in zip(dbs, offsets):
            sa = part.sas["exact"]
            count, left = sa_search(part.texts["exact"], sa, bytes(q))
            if count:
                pos = np.asarray(sa[left : left + count])
                n += len(np.unique(np.searchsorted(part.doc_starts, pos, side="right")))
        return n

    def remote_count(q):
        """(time of the request, SQLite time, files); a timeout (5 s) is (inf, inf, None)."""
        try:
            answer, sql_us = remote.exec(COUNT, [text(phrase(q))])
        except RemoteError as e:
            if e.code != "QUERY_TIMEOUT":
                raise
            return float("inf"), float("inf"), None
        return remote.last_ms, sql_us / 1000, int(answer["rows"][0][0]["value"])

    rows = []
    for label, q in queries(db_dir, per_length):
        sa_ms, files = timed(lambda: sa_count(q))
        fts_ms, fts_files = timed(lambda: con.execute(COUNT, (phrase(q),)).fetchone()[0])
        assert files == fts_files, (label, files, fts_files)
        fts10_ms, _ = timed(lambda: con.execute(FIRST_10, (phrase(q),)).fetchall())
        row = {"label": label, "notes": len(q), "files": files, "sa_ms": sa_ms, "fts_ms": fts_ms,
               "fts10_ms": fts10_ms}
        if hasattr(db, "search"):
            row["app_ms"], _ = timed(lambda: db.search(q, approximate=False))
        if remote:
            remote_count(q)  # first request: opens the connection, loads the pages
            results = [remote_count(q) for _ in range(3)]
            row["remote_sql_ms"] = min(r[1] for r in results)
            row["remote_ms"] = min(r[0] for r in results)
            assert results[0][2] in (files, None), (label, "remote", results[0][2], files)
        rows.append(row)
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}),
              flush=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rows, indent=1))
    summary(rows)


def export_queries(db_dir, results, out, per_length):
    """The queries of bench, with the number of files that bench found, for
    bench/reader.mjs."""
    rows = json.loads(Path(results).read_text())
    qs = queries(db_dir, per_length)
    assert [r["label"] for r in rows] == [label for label, _ in qs], "not the queries of this bench"
    Path(out).write_text(json.dumps([{"label": label, "notes": len(q), "phrase": phrase(q), "files": r["files"]}
                                     for (label, q), r in zip(qs, rows)]))
    print(f"{len(rows)} queries in {out}")


def summary(rows):
    cols = [c for c in ("sa_ms", "fts_ms", "fts10_ms", "app_ms", "remote_sql_ms", "remote_ms")
            if c in rows[0]]
    groups = {}
    for r in rows:
        key = r["label"] if r["label"].startswith("random") else "10 tunes"
        groups.setdefault(key, []).append(r)
    print(f"\n{'queries':12} {'files':>7} " + " ".join(f"{c:>14}" for c in cols))
    for key, rs in groups.items():
        files = np.median([r["files"] for r in rs])
        cells = [f"{np.median([r[c] for r in rs]):7.2f} /{np.max([r[c] for r in rs]):6.1f}" for c in cols]
        print(f"{key:12} {files:7.0f} " + " ".join(f"{c:>14}" for c in cells))
    print("(median / max, in ms)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("local")
    p.add_argument("db_dir")
    p.add_argument("out")
    p = sub.add_parser("upload")
    p.add_argument("db_dir")
    p.add_argument("name")
    p.add_argument("-j", "--jobs", type=int, default=4)
    p.add_argument("--no-optimize", action="store_true")
    p = sub.add_parser("shard", help="the first melodies, up to a size of FTS5 index")
    p.add_argument("db_dir")
    p.add_argument("out_dir")
    p.add_argument("--max-mib", type=float, default=1024 - 40)
    p = sub.add_parser("bench")
    p.add_argument("db_dir")
    p.add_argument("local")
    p.add_argument("name", nargs="?")
    p.add_argument("-n", "--per-length", type=int, default=30)
    p.add_argument("-o", "--out", default="data/bench/fts5.json")
    p = sub.add_parser("queries", help="the queries of a bench run, for bench/reader.mjs")
    p.add_argument("db_dir")
    p.add_argument("results", help="the JSON file of a bench run on db_dir")
    p.add_argument("-o", "--out", default="data/bench/shard-queries.json")
    p.add_argument("-n", "--per-length", type=int, default=30)
    args = ap.parse_args()
    if args.command == "local":
        build_local(args.db_dir, args.out)
    elif args.command == "upload":
        upload(args.db_dir, args.name, args.jobs, not args.no_optimize)
    elif args.command == "queries":
        export_queries(args.db_dir, args.results, args.out, args.per_length)
    elif args.command == "shard":
        shard(args.db_dir, args.out_dir, int(args.max_mib * 2**20))
    else:
        bench(args.db_dir, args.local, args.name, args.per_length, args.out)


if __name__ == "__main__":
    main()
