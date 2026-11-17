"""Suffix array index of the melodies of a MIDI collection.

All the melodies are concatenated in one array of notes, with a separator
after each file. Four texts are built over this array, one per search
mode, so that a position in any text is also a position in the note
array:

    exact             pitch
    octave            pitch % 12
    transpose         interval to the next note + 127
    transpose_octave  interval to the next note % 12

The separator byte (255) is not in any query, so a match never crosses
two files. The suffix arrays are built with pydivsufsort.

On disk, each array is a .npy file. load() memory-maps them, so the
operating system reads only the pages that a search touches.
"""

import json
import os
import shutil
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from pydivsufsort import divsufsort, sa_search
from tqdm import tqdm

from .midi import is_title, parse_midi, ticks_to_seconds

MODES = ("exact", "octave", "transpose", "transpose_octave")
SEP = 255
FORMAT_VERSION = 2
MIDI_SUFFIXES = {".mid", ".midi", ".kar", ".rmi"}

MAX_QUERY_NOTES = 64
MAX_LIMIT = 50
MAX_CONTEXT = 32
SCAN_CAP = 100_000  # max number of matches grouped by file per search
MAX_POSITIONS = 10  # max number of occurrences listed per file


def mode_name(octave: bool, transpose: bool) -> str:
    if transpose:
        return "transpose_octave" if octave else "transpose"
    return "octave" if octave else "exact"


def encode(notes: np.ndarray, mode: str) -> np.ndarray:
    """Encodes an array of notes for a search mode.

    notes: int array, with -1 for the separators.
    Returns a uint8 array of the same length. Positions without a value
    (separators, and in the transpose modes the last note of a file) are SEP.
    """
    notes = np.asarray(notes, dtype=np.int16)
    is_note = notes >= 0
    out = np.full(len(notes), SEP, dtype=np.uint8)
    if mode == "exact":
        out[is_note] = notes[is_note]
    elif mode == "octave":
        out[is_note] = notes[is_note] % 12
    elif mode in ("transpose", "transpose_octave"):
        interval = notes[1:] - notes[:-1]
        valid = is_note[:-1] & is_note[1:]
        interval = interval[valid]
        out[:-1][valid] = interval % 12 if mode == "transpose_octave" else interval + 127
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return out


def encode_query(notes, mode: str) -> np.ndarray:
    """Pattern to search for a list of MIDI numbers."""
    k = len(notes)
    encoded = encode(np.append(np.asarray(notes, dtype=np.int16), -1), mode)
    return encoded[: k - 1] if mode.startswith("transpose") else encoded[:k]


def find_midi_files(root):
    root = Path(root)
    for dirpath, _, filenames in os.walk(root):
        for filename in filenames:
            if Path(filename).suffix.lower() in MIDI_SUFFIXES:
                path = Path(dirpath) / filename
                yield path.relative_to(root).as_posix(), path


def _parse_worker(item):
    name, path = item
    try:
        return name, parse_midi(path), None
    except Exception as e:  # broken files are common in large collections
        return name, None, f"{type(e).__name__}: {e}"


def build(midi_dir, db_dir, jobs=None, progress=True):
    """Parses all the MIDI files under midi_dir and writes the index to db_dir."""
    started = time.time()
    files = sorted(find_midi_files(midi_dir))
    melodies, failed = {}, {}
    with Pool(jobs) as pool:
        results = pool.imap_unordered(_parse_worker, files, chunksize=16)
        for name, melody, error in tqdm(
            results, total=len(files), unit="file", disable=not progress
        ):
            if melody is None:
                failed[name] = error
            elif len(melody):
                melodies[name] = melody
    names = sorted(melodies)
    meta = write_index(
        db_dir,
        names,
        (melodies.pop(n) for n in names),
        progress=progress,
        extra_meta={"failed": len(failed), "parse_seconds": round(time.time() - started, 1)},
    )
    (Path(db_dir) / "failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=1))
    return meta


def write_index(db_dir, names, melodies, sources=None, doc_sources=None,
                doc_copies=None, titles=None, extra_meta=None, progress=True):
    """Writes the index of the melodies (an iterable of Melody, in the
    order of names) to db_dir.

    sources: names of the collections; doc_sources: index in sources of
    the collection of each melody; doc_copies: number of files with each
    melody (more copies rank first when the number of matches is equal);
    titles: title of each file from its text events, or None.

    The index is written to a new directory that then replaces db_dir, so
    a server that has the old index open keeps working.
    """
    started = time.time()
    db_dir = Path(db_dir)
    tmp = db_dir.with_name(db_dir.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    melodies = list(melodies)
    sizes = np.array([len(m) + 1 for m in melodies], dtype=np.int64)
    doc_starts = np.r_[0, np.cumsum(sizes)].astype(np.int64)
    total = int(doc_starts[-1])
    if total >= 2**31 - 1:
        raise ValueError("more than 2**31 notes: not supported")

    notes = np.full(total, -1, dtype=np.int16)
    ticks = np.zeros(total, dtype=np.uint32)
    ppq = np.zeros(len(names), dtype=np.uint32)
    tempo_starts = np.r_[0, np.cumsum([len(m.tempo_ticks) for m in melodies])].astype(np.int64)
    tempo_ticks = np.zeros(tempo_starts[-1], dtype=np.uint32)
    tempo_mspq = np.zeros(tempo_starts[-1], dtype=np.uint32)
    for i, m in enumerate(melodies):
        a, b = doc_starts[i], doc_starts[i + 1] - 1
        notes[a:b] = m.pitches
        ticks[a:b] = m.ticks
        ppq[i] = m.ppq
        c, d = tempo_starts[i], tempo_starts[i + 1]
        tempo_ticks[c:d] = m.tempo_ticks
        tempo_mspq[c:d] = m.tempo_mspq
    del melodies

    for mode in tqdm(MODES, desc="suffix arrays", disable=not progress):
        text = encode(notes, mode)
        sa = divsufsort(text)
        np.save(tmp / f"text_{mode}.npy", text)
        np.save(tmp / f"sa_{mode}.npy", sa.astype(np.int32, copy=False))
        del text, sa
    np.save(tmp / "ticks.npy", ticks)
    np.save(tmp / "tempo_starts.npy", tempo_starts)
    np.save(tmp / "tempo_ticks.npy", tempo_ticks)
    np.save(tmp / "tempo_mspq.npy", tempo_mspq)
    np.save(tmp / "doc_starts.npy", doc_starts)
    np.save(tmp / "ppq.npy", ppq)
    (tmp / "names.json").write_text(json.dumps(list(names), ensure_ascii=False))
    if sources is not None:
        (tmp / "sources.json").write_text(json.dumps(list(sources)))
        np.save(tmp / "doc_sources.npy", np.asarray(doc_sources, dtype=np.uint16))
    if doc_copies is not None:
        np.save(tmp / "doc_copies.npy", np.asarray(doc_copies, dtype=np.uint32))
    if titles is not None:
        (tmp / "titles.json").write_text(json.dumps(list(titles), ensure_ascii=False))
    meta = {
        "version": FORMAT_VERSION,
        "files": len(names),
        "notes": total - len(names),
        **(extra_meta or {}),
        "index_seconds": round(time.time() - started, 1),
    }
    (tmp / "meta.json").write_text(json.dumps(meta, indent=1))

    old = db_dir.with_name(db_dir.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if db_dir.exists():
        db_dir.rename(old)
    tmp.rename(db_dir)
    shutil.rmtree(old, ignore_errors=True)
    return meta


def _check_query(notes, limit):
    if not notes:
        raise ValueError("empty query")
    if len(notes) > MAX_QUERY_NOTES:
        raise ValueError(f"at most {MAX_QUERY_NOTES} notes")
    return max(1, min(int(limit), MAX_LIMIT))


class Database:
    """One index: at most 2**31 notes (32-bit suffix arrays)."""

    def __init__(self, db_dir):
        db_dir = Path(db_dir)
        self.meta = json.loads((db_dir / "meta.json").read_text())
        if self.meta.get("version") != FORMAT_VERSION:
            raise ValueError(f"{db_dir}: index format {self.meta.get('version')}, "
                             f"expected {FORMAT_VERSION}. Run `index` again.")
        self.names = json.loads((db_dir / "names.json").read_text())

        def load(name):
            # "c" (copy-on-write) and not "r": sa_search needs writable arrays
            return np.load(db_dir / f"{name}.npy", mmap_mode="c")

        self.texts = {mode: load(f"text_{mode}") for mode in MODES}
        self.sas = {mode: load(f"sa_{mode}") for mode in MODES}
        self.ticks = load("ticks")
        self.tempo_starts = np.load(db_dir / "tempo_starts.npy")
        self.tempo_ticks = load("tempo_ticks")
        self.tempo_mspq = load("tempo_mspq")
        self.doc_starts = np.load(db_dir / "doc_starts.npy")
        self.ppq = np.load(db_dir / "ppq.npy")
        self.sources, self.doc_sources = None, None
        if (db_dir / "sources.json").exists():
            self.sources = json.loads((db_dir / "sources.json").read_text())
            self.doc_sources = np.load(db_dir / "doc_sources.npy")
        self.copies = None
        if (db_dir / "doc_copies.npy").exists():
            self.copies = np.load(db_dir / "doc_copies.npy")
        self.titles = None
        if (db_dir / "titles.json").exists():
            self.titles = json.loads((db_dir / "titles.json").read_text())

    def source(self, doc: int):
        """Name of the collection of a file, or None."""
        return None if self.sources is None else self.sources[self.doc_sources[doc]]

    def seconds(self, doc: int, positions) -> np.ndarray:
        """Time in seconds of notes of a file (positions local to the file)."""
        c, d = self.tempo_starts[doc], self.tempo_starts[doc + 1]
        ticks = self.ticks[self.doc_starts[doc] + np.asarray(positions, dtype=np.int64)]
        return ticks_to_seconds(ticks, self.tempo_ticks[c:d], self.tempo_mspq[c:d], int(self.ppq[doc]))

    def doc_length(self, doc: int) -> int:
        return int(self.doc_starts[doc + 1] - self.doc_starts[doc] - 1)

    def find(self, notes, octave=False, transpose=False):
        """Returns (count, positions) where positions are the first
        matches (at most SCAN_CAP) in the global note array."""
        mode = mode_name(octave, transpose)
        pattern = encode_query(notes, mode)
        if len(pattern) == 0:
            raise ValueError("a search with transposition needs at least 2 notes")
        count, left = sa_search(self.texts[mode], self.sas[mode], pattern.tobytes())
        if count == 0:
            return 0, np.zeros(0, dtype=np.int64)
        scanned = self.sas[mode][left : left + min(count, SCAN_CAP)]
        return count, np.asarray(scanned, dtype=np.int64)

    def groups(self, notes, octave=False, transpose=False, limit=10):
        """Matches grouped by file, for the `limit` best files.

        Returns (number of matches, number of files, [(score,
        occurrences, copies, doc, positions in the file), ...]), best
        score first: log2(1 + occurrences) + log2(copies). A melody that
        many files have is probably a well-known song; a file that repeats
        the query many times is often a loop or an exercise, so the number
        of occurrences counts less and less.
        """
        count, positions = self.find(notes, octave, transpose)
        if count == 0:
            return 0, 0, []
        docs = np.searchsorted(self.doc_starts, positions, side="right") - 1
        order = np.lexsort((positions, docs))
        docs, positions = docs[order], positions[order]
        firsts = np.flatnonzero(np.r_[True, docs[1:] != docs[:-1]])
        counts = np.diff(np.r_[firsts, len(docs)])
        uniq = docs[firsts]
        copies = self.copies[uniq].astype(np.int64) if self.copies is not None else np.ones_like(uniq)
        score = np.log2(1 + counts) + np.log2(np.maximum(copies, 1))
        best = np.lexsort((uniq, -score))[:limit]
        return int(count), len(firsts), [
            (float(score[i]), int(counts[i]), int(copies[i]), int(uniq[i]),
             positions[firsts[i] : firsts[i] + counts[i]] - self.doc_starts[uniq[i]])
            for i in best
        ]

    def result(self, doc, positions, length, context, occurrences, copies):
        """One file of the search result (positions local to the file)."""
        shown = positions[:MAX_POSITIONS]
        return {
            "doc": doc,
            "file": self.names[doc],
            # the rules for titles may be newer than the index
            "title": self.titles[doc] if self.titles and is_title(self.titles[doc]) else None,
            "source": self.source(doc),
            "copies": copies,
            "occurrences": occurrences,
            "positions": [
                {"pos": int(p), "sec": round(float(s), 2)}
                for p, s in zip(shown, self.seconds(doc, shown))
            ],
            **self.context(doc, int(positions[0]), length, context),
        }

    def search(self, notes, octave=False, transpose=False, limit=10, context=8):
        """Searches a melody and groups the matches by file (at most `limit` files)."""
        limit = _check_query(notes, limit)
        count, files, groups = self.groups(notes, octave, transpose, limit)
        return {
            "total": count,
            "files_total": files,
            "files_exact": count <= SCAN_CAP,
            "results": [
                self.result(doc, positions, len(notes), context, occurrences, copies)
                for _, occurrences, copies, doc, positions in groups
            ],
        }

    def context(self, doc: int, pos: int, length: int, context: int = 8):
        """Notes of a file around a match (positions are local to the file)."""
        if not 0 <= doc < len(self.names):
            raise ValueError("unknown file")
        n = self.doc_length(doc)
        if not (0 <= pos < n and 1 <= length <= MAX_QUERY_NOTES and pos + length <= n):
            raise ValueError("match outside the file")
        context = max(0, min(int(context), MAX_CONTEXT))
        start = max(0, pos - context)
        end = min(n, pos + length + context)
        a = self.doc_starts[doc]
        return {
            "match": {"pos": pos, "len": length},
            "start": start,
            "ppq": int(self.ppq[doc]),
            "notes": [
                {"p": int(p), "tick": int(t), "sec": round(float(s), 3)}
                for p, t, s in zip(
                    self.texts["exact"][a + start : a + end],
                    self.ticks[a + start : a + end],
                    self.seconds(doc, np.arange(start, end)),
                )
            ],
        }


class _Names:
    """The file names of several parts, as one list."""

    def __init__(self, parts, offsets):
        self.parts, self.offsets = parts, offsets

    def __len__(self):
        return int(self.offsets[-1])

    def __getitem__(self, doc):
        k = int(np.searchsorted(self.offsets, doc, side="right") - 1)
        return self.parts[k].names[doc - self.offsets[k]]


class MultiDatabase:
    """An index in several parts (db_dir/part-NNN), searched as one.
    File numbers continue from one part to the next."""

    def __init__(self, parts):
        self.parts = parts
        self.offsets = np.r_[0, np.cumsum([len(p.names) for p in parts])]
        self.names = _Names(parts, self.offsets)
        self.meta = {
            "version": FORMAT_VERSION,
            "parts": len(parts),
            "files": sum(p.meta["files"] for p in parts),
            "notes": sum(p.meta["notes"] for p in parts),
        }

    def _locate(self, doc):
        if not 0 <= doc < len(self.names):
            raise ValueError("unknown file")
        k = int(np.searchsorted(self.offsets, doc, side="right") - 1)
        return self.parts[k], int(doc - self.offsets[k])

    def source(self, doc):
        part, local = self._locate(doc)
        return part.source(local)

    def context(self, doc, pos, length, context=8):
        part, local = self._locate(doc)
        return part.context(local, pos, length, context)

    def search(self, notes, octave=False, transpose=False, limit=10, context=8):
        limit = _check_query(notes, limit)
        total, files, exact, groups = 0, 0, True, []
        for k, part in enumerate(self.parts):
            count, n_files, part_groups = part.groups(notes, octave, transpose, limit)
            total, files, exact = total + count, files + n_files, exact and count <= SCAN_CAP
            groups += [(score, occ, copies, int(self.offsets[k] + doc), k, doc, positions)
                       for score, occ, copies, doc, positions in part_groups]
        groups.sort(key=lambda g: (-g[0], g[3]))
        results = []
        for _, occ, copies, global_doc, k, doc, positions in groups[:limit]:
            result = self.parts[k].result(doc, positions, len(notes), context, occ, copies)
            results.append({**result, "doc": global_doc})
        return {"total": total, "files_total": files, "files_exact": exact, "results": results}


def open_database(db_dir):
    """Database or MultiDatabase, from what db_dir contains."""
    db_dir = Path(db_dir)
    if (db_dir / "meta.json").exists():
        return Database(db_dir)
    parts = sorted(db_dir.glob("part-*"))
    if not parts:
        raise FileNotFoundError(f"{db_dir}: no index (meta.json or part-NNN/)")
    return MultiDatabase([Database(p) for p in parts])
