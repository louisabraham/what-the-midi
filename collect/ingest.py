"""Reads the MIDI files of a source, removes duplicates, and stores melodies.

The files are read from the archives directly (zip, tar, nested zip), or
from a temporary extraction for 7z archives. No MIDI file is written to
the disk.

Two duplicate checks, in this order:
1. Same bytes (MD5 of the file): the file is not parsed again.
2. Same melody (hash of the pitch sequence that the index stores): the
   file adds nothing to the index. The first source in sources.SOURCES
   keeps its name.

Results go to data/store/:
    catalog.sqlite              one row per unique file and per melody
    melodies/<source>-NNN.npz   melodies that the index will contain
"""

import hashlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.midi import midi_title, parse_midi_bytes  # noqa: E402

MIN_NOTES = 8  # shorter melodies are not useful for a search
BATCH = 2000  # files parsed in parallel at a time
SHARD_NOTES = 50_000_000  # notes per melody shard file

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    md5 TEXT PRIMARY KEY,
    source TEXT, path TEXT, size INTEGER,
    status TEXT,          -- ok, dup_melody, short, error
    melody TEXT,          -- melody hash
    notes INTEGER,
    error TEXT,
    title TEXT            -- from the text events of the file
);
CREATE TABLE IF NOT EXISTS aliases (  -- other paths of the same bytes
    md5 TEXT, source TEXT, path TEXT
);
CREATE TABLE IF NOT EXISTS melodies (
    hash TEXT PRIMARY KEY,
    md5 TEXT, source TEXT, path TEXT, notes INTEGER, shard TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    name TEXT PRIMARY KEY,
    files INTEGER, dup_bytes INTEGER, dup_melody INTEGER, short INTEGER,
    errors INTEGER, melodies INTEGER, notes INTEGER, seconds REAL
);
"""


def connect(store: Path):
    store.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(store / "catalog.sqlite", timeout=60)
    db.execute("PRAGMA journal_mode=WAL")  # reads during an ingestion
    db.executescript(SCHEMA)
    return db


# ---------------------------------------------------------------- reading


def _is_midi(data: bytes):
    return data[:4] == b"MThd"


def _iter_zip(zf: zipfile.ZipFile, prefix: str):
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        try:
            data = zf.read(info)
        except Exception as e:  # broken member
            print(f"  cannot read {prefix}{name}: {e}", file=sys.stderr)
            continue
        if name.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as nested:
                    yield from _iter_zip(nested, f"{prefix}{name[:-4]}/")
            except zipfile.BadZipFile:
                pass
        elif _is_midi(data):
            yield prefix + name, data


def iter_files(path: Path, tmp_dir: Path):
    """Yields (path inside the archive, bytes) for each MIDI file."""
    name = path.name.lower()
    if path.is_dir():
        for root, _, files in os.walk(path):
            for f in sorted(files):
                p = Path(root) / f
                data = p.read_bytes()
                if _is_midi(data):
                    yield p.relative_to(path).as_posix(), data
    elif name.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            yield from _iter_zip(zf, "")
    elif name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")):
        with tarfile.open(path, "r|*") as tf:  # streaming
            for member in tf:
                if member.isfile():
                    data = tf.extractfile(member).read()
                    if _is_midi(data):
                        yield member.name, data
    elif name.endswith(".7z"):
        # Python has no fast 7z reader: extract with bsdtar (libarchive)
        out = Path(tempfile.mkdtemp(dir=tmp_dir))
        try:
            subprocess.run(["bsdtar", "-xf", str(path), "-C", str(out)], check=True)
            yield from iter_files(out, tmp_dir)
        finally:
            shutil.rmtree(out, ignore_errors=True)
    elif _is_midi(path.read_bytes()[:4]):
        yield path.name, path.read_bytes()


# ---------------------------------------------------------------- parsing


def _parse(item):
    md5, data = item
    title = midi_title(data)
    try:
        melody = parse_midi_bytes(data)
    except Exception as e:
        return md5, None, f"{type(e).__name__}: {e}"[:300], title
    return md5, melody, None, title


def melody_hash(pitches: np.ndarray) -> str:
    return hashlib.blake2b(pitches.tobytes(), digest_size=16).hexdigest()


class ShardWriter:
    def __init__(self, directory: Path, source: str):
        self.directory = directory
        self.source = source
        directory.mkdir(parents=True, exist_ok=True)
        self.index = len(list(directory.glob(f"{source}-*.npz")))
        self.reset()

    def reset(self):
        self.items = []
        self.notes = 0

    @property
    def name(self):
        return f"{self.source}-{self.index:03d}"

    def add(self, key, path, melody):
        self.items.append((key, path, melody))
        self.notes += len(melody)
        if self.notes >= SHARD_NOTES:
            self.flush()

    def flush(self):
        if not self.items:
            return
        keys, paths, melodies = zip(*self.items)
        lengths = np.array([len(m) for m in melodies], dtype=np.int64)
        tempo_lengths = np.array([len(m.tempo_ticks) for m in melodies], dtype=np.int64)
        np.savez_compressed(
            self.directory / f"{self.name}.npz",
            keys=np.array(keys),
            paths=np.array(paths),
            offsets=np.r_[0, np.cumsum(lengths)],
            ppq=np.array([m.ppq for m in melodies], dtype=np.uint32),
            pitches=np.concatenate([m.pitches for m in melodies]),
            ticks=np.concatenate([m.ticks for m in melodies]),
            tempo_offsets=np.r_[0, np.cumsum(tempo_lengths)],
            tempo_ticks=np.concatenate([m.tempo_ticks for m in melodies]),
            tempo_mspq=np.concatenate([m.tempo_mspq for m in melodies]),
        )
        self.index += 1
        self.reset()


def forget(db, store: Path, source_name: str):
    """Removes a source from the store."""
    for table in ("files", "aliases", "melodies"):
        db.execute(f"DELETE FROM {table} WHERE source = ?", (source_name,))
    db.execute("DELETE FROM sources WHERE name = ?", (source_name,))
    db.commit()
    for shard in (store / "melodies").glob(f"{source_name}-*.npz"):
        shard.unlink()


def ingest(source_name: str, paths, store: Path, jobs=None, max_notes=None):
    """Adds the MIDI files of the archives `paths` to the store.

    Stops when the new melodies reach max_notes notes (the disk budget).
    """
    db = connect(store)
    if db.execute("SELECT 1 FROM sources WHERE name = ?", (source_name,)).fetchone():
        print(f"{source_name}: already ingested (run with --redo to ingest it again)")
        return
    forget(db, store, source_name)  # remove what an interrupted run wrote
    tmp_dir = store / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    started = time.time()
    seen_md5 = {row[0] for row in db.execute("SELECT md5 FROM files")}
    seen_melody = {row[0] for row in db.execute("SELECT hash FROM melodies")}
    writer = ShardWriter(store / "melodies", source_name)
    stats = dict(files=0, dup_bytes=0, dup_melody=0, short=0, errors=0, melodies=0, notes=0)
    if max_notes is not None and max_notes <= 0:
        print(f"{source_name}: no disk budget left, not ingested")
        return None

    def handle(batch, pool):
        rows, melody_rows = [], []
        for md5, melody, error, title in pool.map(_parse, [(m, d) for m, _, d in batch], chunksize=16):
            path, size = info[md5]
            if error:
                stats["errors"] += 1
                rows.append((md5, source_name, path, size, "error", None, None, error, title))
            elif len(melody) < MIN_NOTES:
                stats["short"] += 1
                rows.append((md5, source_name, path, size, "short", None, len(melody), None, title))
            else:
                key = melody_hash(melody.pitches)
                if key in seen_melody:
                    stats["dup_melody"] += 1
                    status = "dup_melody"
                else:
                    seen_melody.add(key)
                    writer.add(key, path, melody)
                    melody_rows.append((key, md5, source_name, path, len(melody), writer.name))
                    stats["melodies"] += 1
                    stats["notes"] += len(melody)
                    status = "ok"
                    if max_notes is not None and stats["notes"] >= max_notes:
                        stats["truncated"] = True
                rows.append((md5, source_name, path, size, status, key, len(melody), None, title))
        db.executemany("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.executemany("INSERT INTO melodies VALUES (?, ?, ?, ?, ?, ?)", melody_rows)
        info.clear()

    info = {}
    batch, aliases = [], []
    with Pool(jobs) as pool:
        progress = tqdm(unit=" files", desc=source_name, mininterval=2)
        for archive in paths:
            for path, data in iter_files(Path(archive), tmp_dir):
                stem = Path(archive).stem
                if len(paths) > 1 and not Path(archive).is_dir() and not path.startswith(stem + "/"):
                    path = f"{stem}/{path}"
                stats["files"] += 1
                progress.update()
                md5 = hashlib.md5(data).hexdigest()
                if md5 in seen_md5:
                    stats["dup_bytes"] += 1
                    aliases.append((md5, source_name, path))
                    continue
                seen_md5.add(md5)
                info[md5] = (path, len(data))
                batch.append((md5, path, data))
                if len(batch) >= BATCH:
                    handle(batch, pool)
                    batch = []
                    progress.set_postfix(melodies=stats["melodies"], dup=stats["dup_bytes"] + stats["dup_melody"])
                    if stats.get("truncated"):
                        break
            if stats.get("truncated"):
                print(f"{source_name}: note budget reached, the rest of the source is not ingested")
                break
        if batch:
            handle(batch, pool)
        progress.close()
    writer.flush()
    db.executemany("INSERT INTO aliases VALUES (?, ?, ?)", aliases)
    db.execute(
        "INSERT INTO sources VALUES (:name, :files, :dup_bytes, :dup_melody, :short, :errors, :melodies, :notes, :seconds)",
        dict(stats, name=source_name, seconds=round(time.time() - started, 1)),
    )
    db.commit()
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return stats
