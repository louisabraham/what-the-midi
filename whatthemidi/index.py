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
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from pydivsufsort import divsufsort, sa_search
from tqdm import tqdm

from .midi import parse_midi

MODES = ("exact", "octave", "transpose", "transpose_octave")
SEP = 255
FORMAT_VERSION = 1
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
    db_dir = Path(db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
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
    parsed = time.time()

    sizes = np.array([len(melodies[n]) + 1 for n in names], dtype=np.int64)
    doc_starts = np.r_[0, np.cumsum(sizes)].astype(np.int64)
    total = int(doc_starts[-1])
    if total >= 2**31 - 1:
        raise ValueError("more than 2**31 notes: not supported")

    notes = np.full(total, -1, dtype=np.int16)
    ticks = np.zeros(total, dtype=np.uint32)
    seconds = np.zeros(total, dtype=np.float32)
    ppq = np.zeros(len(names), dtype=np.uint32)
    for i, name in enumerate(names):
        m = melodies.pop(name)
        a, b = doc_starts[i], doc_starts[i + 1] - 1
        notes[a:b] = m.pitches
        ticks[a:b] = m.ticks
        seconds[a:b] = m.seconds
        ppq[i] = m.ppq

    for mode in tqdm(MODES, desc="suffix arrays", disable=not progress):
        text = encode(notes, mode)
        sa = divsufsort(text)
        np.save(db_dir / f"text_{mode}.npy", text)
        np.save(db_dir / f"sa_{mode}.npy", sa.astype(np.int32, copy=False))
    np.save(db_dir / "ticks.npy", ticks)
    np.save(db_dir / "seconds.npy", seconds)
    np.save(db_dir / "doc_starts.npy", doc_starts)
    np.save(db_dir / "ppq.npy", ppq)
    (db_dir / "names.json").write_text(json.dumps(names, ensure_ascii=False))
    (db_dir / "failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=1))
    meta = {
        "version": FORMAT_VERSION,
        "files": len(names),
        "notes": total - len(names),
        "failed": len(failed),
        "parse_seconds": round(parsed - started, 1),
        "index_seconds": round(time.time() - parsed, 1),
    }
    (db_dir / "meta.json").write_text(json.dumps(meta, indent=1))
    return meta


class Database:
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
        self.seconds = load("seconds")
        self.doc_starts = np.load(db_dir / "doc_starts.npy")
        self.ppq = np.load(db_dir / "ppq.npy")

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

    def search(self, notes, octave=False, transpose=False, limit=10, context=8):
        """Searches a melody and groups the matches by file.

        Files with the most matches come first. At most `limit` files.
        """
        if not notes:
            raise ValueError("empty query")
        if len(notes) > MAX_QUERY_NOTES:
            raise ValueError(f"at most {MAX_QUERY_NOTES} notes")
        limit = max(1, min(int(limit), MAX_LIMIT))
        count, positions = self.find(notes, octave, transpose)
        if count == 0:
            return {"total": 0, "files_total": 0, "files_exact": True, "results": []}

        docs = np.searchsorted(self.doc_starts, positions, side="right") - 1
        order = np.lexsort((positions, docs))
        docs, positions = docs[order], positions[order]
        firsts = np.flatnonzero(np.r_[True, docs[1:] != docs[:-1]])
        counts = np.diff(np.r_[firsts, len(docs)])
        best = np.lexsort((docs[firsts], -counts))[:limit]

        results = []
        for i in best:
            doc = int(docs[firsts[i]])
            local = positions[firsts[i] : firsts[i] + counts[i]] - self.doc_starts[doc]
            shown = local[:MAX_POSITIONS]
            seconds = self.seconds[self.doc_starts[doc] + shown]
            results.append({
                "doc": doc,
                "file": self.names[doc],
                "occurrences": int(counts[i]),
                "positions": [
                    {"pos": int(p), "sec": round(float(s), 2)}
                    for p, s in zip(shown, seconds)
                ],
                **self.context(doc, int(local[0]), len(notes), context),
            })
        return {
            "total": int(count),
            "files_total": len(firsts),
            "files_exact": count <= SCAN_CAP,
            "results": results,
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
                    self.seconds[a + start : a + end],
                )
            ],
        }
