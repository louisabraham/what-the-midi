"""Builds the search index from the melodies in data/store/melodies/.

One index entry per group of melodies (see neardup.py; without
store/neardup.npz, each melody is its own group):

- melody: the melody of the group with the most copies, then the longest
- name: the best path among all the files of the group: not a checksum,
  then a file name with words (see _is_cryptic), then the first source in
  sources.SOURCES, then the shortest. When all the paths are checksums,
  the name is the title.
- title: the GigaMIDI metadata ("Artist - Title", found by MD5), else the
  title written in a file (its text events)
- copies: the number of files of the group (a melody found in many files
  is probably a well-known song); versions: the number of melodies
"""

import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.index import write_index  # noqa: E402
from whatthemidi.midi import Melody, is_title  # noqa: E402

from ingest import connect  # noqa: E402
from metadata import display_title  # noqa: E402
from sources import SOURCES  # noqa: E402

# notes per index part: a part must stay under 2**31 notes (32-bit suffix arrays)
PART_NOTES = 1_500_000_000
HASH_NAME = re.compile(r"^[0-9a-f]{32,64}$", re.I)


_EXTENSIONS = re.compile(r"(\.(mid|midi|kar|rmi))+$", re.I)


def _stem(path):
    """File name without its MIDI extensions ("a.mid.mid" -> "a")."""
    return _EXTENSIONS.sub("", path.rsplit("/", 1)[-1])


def _is_hash(path):
    """True for a file named by a checksum (Lakh full, Los Angeles...)."""
    return bool(HASH_NAME.match(_stem(path)))


def _is_cryptic(path):
    """True for a file name that says little: fewer than 4 letters, or more
    digits than letters ("47746575hnn (36385).mid", "s1003.mid")."""
    stem = _stem(path)
    letters = sum(c.isalpha() for c in stem)
    return letters < 4 or sum(c.isdigit() for c in stem) > letters


def file_names(db):
    """For each melody hash: its best (rank, source, path), its best
    (rank, title), and its number of files."""
    priority = {s.name: i for i, s in enumerate(SOURCES)}
    best, title, copies = {}, {}, Counter()
    rows = db.execute("""
        SELECT f.melody, f.source, f.path, f.title, m.title, m.artist
        FROM files f LEFT JOIN metadata m ON m.md5 = f.md5 WHERE f.melody IS NOT NULL
        UNION ALL
        SELECT f.melody, a.source, a.path, f.title, m.title, m.artist
        FROM aliases a JOIN files f ON a.md5 = f.md5 LEFT JOIN metadata m ON m.md5 = f.md5
        WHERE f.melody IS NOT NULL
    """)
    for key, source, path, file_title, meta_title, meta_artist in rows:
        copies[key] += 1
        p = priority.get(source, len(priority))
        rank = (_is_hash(path), _is_cryptic(path), p, len(path), path)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, source, path)
        candidates = []
        if meta_title:
            candidates.append(((0, p), display_title(meta_title, meta_artist)))
        if is_title(file_title):  # the rules may be newer than the ingestion
            candidates.append(((1, p), file_title))
        for t_rank, t in candidates:
            if key not in title or t_rank < title[key][0]:
                title[key] = (t_rank, t)
    return best, title, copies


def _strip_common_dir(paths):
    """Removes the first directory when all the paths have the same one
    (e.g. "clean_midi/" in Lakh)."""
    first = {p.split("/", 1)[0] if "/" in p else None for p in paths}
    if len(first) == 1 and None not in first:
        return [p.split("/", 1)[1] for p in paths]
    return list(paths)


def load_shards(store: Path, sources):
    """Yields (melody hash, Melody) of the shards of these sources."""
    for source in sources:
        for shard in sorted((store / "melodies").glob(f"{source}-*.npz")):
            with np.load(shard) as z:
                keys, off, ppq = z["keys"], z["offsets"], z["ppq"]
                pitches, ticks = z["pitches"], z["ticks"]
                t_off, t_ticks, t_mspq = z["tempo_offsets"], z["tempo_ticks"], z["tempo_mspq"]
            for k in range(len(keys)):
                a, b = off[k], off[k + 1]
                c, d = t_off[k], t_off[k + 1]
                yield str(keys[k]), Melody(pitches[a:b], ticks[a:b], int(ppq[k]),
                                           t_ticks[c:d], t_mspq[c:d])


def load_groups(store: Path):
    """{melody hash: group hash} from neardup.py, or {}."""
    path = store / "neardup.npz"
    if not path.exists():
        return {}
    with np.load(path) as z:
        return dict(zip(z["keys"].tolist(), z["group"].tolist()))


def build(store: Path, db_dir: Path, only=None, part_notes=PART_NOTES):
    present = {p.name.rsplit("-", 1)[0] for p in (store / "melodies").glob("*.npz")}
    sources = [s.name for s in SOURCES if s.name in present and (not only or s.name in only)]
    print("sources:", ", ".join(sources))
    best, title, copies = file_names(connect(store))
    groups = load_groups(store)

    members = defaultdict(list)  # group -> [(key, Melody)]
    for key, melody in load_shards(store, sources):
        members[groups.get(key, key)].append((key, melody))
    n_melodies = sum(map(len, members.values()))

    names, titles, doc_source, from_title, doc_copies, versions, melodies = [], [], [], [], [], [], []
    for group in members.values():
        keys = [k for k, _ in group]
        # the melody: most copies, then the longest
        _, melody = max(group, key=lambda km: (copies[km[0]], len(km[1])))
        rank, source, path = min(best[k] for k in keys)
        t = min((title[k] for k in keys if k in title), default=(None, None))[1]
        use_title = bool(rank[0] and t)  # only checksum paths: the title is the name
        names.append(t.replace("/", "∕") if use_title else path)
        titles.append(t)
        doc_source.append(source)
        from_title.append(use_title)
        doc_copies.append(sum(copies[k] for k in keys))
        versions.append(len(keys))
        melodies.append(melody)
    del members
    print(f"{n_melodies:,} melodies in {len(melodies):,} groups, "
          f"{sum(map(len, melodies)):,} notes to index")

    # names, without the top directory common to all the files of a source
    all_sources = sorted(set(doc_source), key=[s.name for s in SOURCES].index)
    by_source = defaultdict(list)
    for i, s in enumerate(doc_source):
        if not from_title[i]:
            by_source[s].append(i)
    for idx in by_source.values():
        for i, p in zip(idx, _strip_common_dir([names[i] for i in idx])):
            names[i] = p

    source_index = {s: i for i, s in enumerate(all_sources)}
    doc_sources = [source_index[s] for s in doc_source]
    extra = dict(sources=all_sources)

    bounds, size = [0], 0
    for i, m in enumerate(melodies):
        if size + len(m) + 1 > part_notes and i > bounds[-1]:
            bounds.append(i)
            size = 0
        size += len(m) + 1
    bounds.append(len(melodies))

    def part(path, a, b):
        return write_index(path, names[a:b], melodies[a:b], doc_sources=doc_sources[a:b],
                           doc_copies=doc_copies[a:b], titles=titles[a:b],
                           doc_versions=versions[a:b], **extra)

    if len(bounds) == 2:
        return part(db_dir, 0, len(melodies))
    print(f"{len(bounds) - 1} parts")
    tmp = db_dir.with_name(db_dir.name + ".parts")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    metas = [part(tmp / f"part-{k:03d}", a, b) for k, (a, b) in enumerate(zip(bounds, bounds[1:]))]
    shutil.rmtree(db_dir, ignore_errors=True)
    tmp.rename(db_dir)
    return {"parts": len(metas), "files": sum(m["files"] for m in metas),
            "notes": sum(m["notes"] for m in metas)}
