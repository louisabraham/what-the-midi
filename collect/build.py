"""Builds the search index from the melodies in data/store/melodies/.

Each melody gets the best name among all the files that have it: the
first source in sources.SOURCES, then the shortest path. The index also
stores how many files have each melody (its copies): a melody with many
copies is probably a well-known song.
"""

import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.index import write_index  # noqa: E402

# notes per index part: a part must stay under 2**31 notes (32-bit suffix arrays)
PART_NOTES = 1_500_000_000
from whatthemidi.midi import Melody, is_title  # noqa: E402

from ingest import connect  # noqa: E402
from sources import SOURCES  # noqa: E402


HASH_NAME = re.compile(r"^[0-9a-f]{32,64}$", re.I)


def _is_hash(path):
    """True for a file named by a checksum (Lakh full, Los Angeles...)."""
    return bool(HASH_NAME.match(path.rsplit("/", 1)[-1].rsplit(".", 1)[0]))


def best_names(db):
    """{melody hash: (source, name, title, name is the title)} and
    {melody hash: number of files}.

    The name is the path of a file with this melody: the first source in
    sources.SOURCES, then the shortest path. Paths that are checksums come
    last: when all the paths are checksums, the name is the title found
    in the files. The title comes from the text events of the files.
    """
    priority = {s.name: i for i, s in enumerate(SOURCES)}
    best, title, copies = {}, {}, Counter()
    rows = db.execute("""
        SELECT melody, source, path, title FROM files WHERE melody IS NOT NULL
        UNION ALL
        SELECT f.melody, a.source, a.path, f.title FROM aliases a JOIN files f ON a.md5 = f.md5
        WHERE f.melody IS NOT NULL
    """)
    for key, source, path, file_title in rows:
        copies[key] += 1
        if not is_title(file_title):  # the rules may be newer than the ingestion
            file_title = None
        rank = (_is_hash(path), priority.get(source, len(priority)), len(path), path)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, source, path, file_title)
        if file_title:
            t_rank = (priority.get(source, len(priority)), file_title)
            if key not in title or t_rank < title[key]:
                title[key] = t_rank
    out = {}
    for key, (rank, source, path, file_title) in best.items():
        t = file_title or (title[key][1] if key in title else None)
        from_title = bool(rank[0] and t)  # only checksum names: use the title
        out[key] = (source, t.replace("/", "\u2215") if from_title else path, t, from_title)
    return out, copies


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


def build(store: Path, db_dir: Path, only=None, part_notes=PART_NOTES):
    present = {p.name.rsplit("-", 1)[0] for p in (store / "melodies").glob("*.npz")}
    sources = [s.name for s in SOURCES if s.name in present and (not only or s.name in only)]
    print("sources:", ", ".join(sources))
    names, copies = best_names(connect(store))

    keys, melodies = [], []
    for key, melody in load_shards(store, sources):
        keys.append(key)
        melodies.append(melody)
    print(f"{len(keys):,} melodies, {sum(map(len, melodies)):,} notes")

    # names, without the top directory common to all the files of a source
    doc_source = [names[k][0] for k in keys]
    paths = [names[k][1] for k in keys]
    titles = [names[k][2] for k in keys]
    from_title = [names[k][3] for k in keys]
    all_sources = sorted(set(doc_source), key=[s.name for s in SOURCES].index)
    for source in all_sources:
        idx = [i for i, s in enumerate(doc_source) if s == source and not from_title[i]]
        for i, p in zip(idx, _strip_common_dir([paths[i] for i in idx])):
            paths[i] = p

    source_index = {s: i for i, s in enumerate(all_sources)}
    doc_sources = [source_index[s] for s in doc_source]
    doc_copies = [copies[k] for k in keys]

    bounds, size = [0], 0
    for i, m in enumerate(melodies):
        if size + len(m) + 1 > part_notes and i > bounds[-1]:
            bounds.append(i)
            size = 0
        size += len(m) + 1
    bounds.append(len(melodies))
    if len(bounds) == 2:
        return write_index(db_dir, paths, melodies, sources=all_sources,
                           doc_sources=doc_sources, doc_copies=doc_copies, titles=titles)

    print(f"{len(bounds) - 1} parts")
    tmp = db_dir.with_name(db_dir.name + ".parts")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    metas = []
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        metas.append(write_index(tmp / f"part-{k:03d}", paths[a:b], melodies[a:b],
                                 sources=all_sources, doc_sources=doc_sources[a:b],
                                 doc_copies=doc_copies[a:b], titles=titles[a:b]))
    shutil.rmtree(db_dir, ignore_errors=True)
    tmp.rename(db_dir)
    return {"parts": len(metas), "files": sum(m["files"] for m in metas),
            "notes": sum(m["notes"] for m in metas)}
