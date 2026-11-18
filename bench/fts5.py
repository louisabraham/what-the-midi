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
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from pydivsufsort import sa_search

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.index import open_database  # noqa: E402
from whatthemidi.notes import parse_query  # noqa: E402

HOST = "sqlite4all.reduck.ai"
SCHEMA = "CREATE VIRTUAL TABLE m USING fts5(grams, content='', columnsize=0)"
MAX_BODY = 240_000  # bytes; the API accepts 256 KiB


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
        """(status, JSON answer, server SQL time in µs); retries on 429/503."""
        for attempt in range(20):
            data = json.dumps(body).encode() if body is not None else None
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
            if r.status in (429, 503):
                time.sleep(float(r.getheader("Retry-After") or 1 + attempt))
                continue
            sql_us = int(r.getheader("X-Sqlite-Time-Us") or 0)
            return r.status, (json.loads(answer) if answer else None), sql_us
        raise RuntimeError(f"{method} {path}: too many retries")

    def exec(self, sql, params=(), timeout_ms=5000):
        status, answer, sql_us = self.call("POST", f"/v1/databases/{self.name}/exec", {
            "sql": sql, "params": list(params), "timeout_ms": timeout_ms})
        if status != 200:
            raise RuntimeError(answer)
        return answer, sql_us

    def batch(self, statements):
        status, answer, sql_us = self.call("POST", f"/v1/databases/{self.name}/batch",
                                           {"statements": statements})
        if status != 200:
            raise RuntimeError(answer)
        return sql_us


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


def upload(db_dir, name, jobs=4):
    first = Remote(name)
    status, answer, _ = first.call("POST", "/v1/databases", {"name": name})
    if status not in (201, 409):
        raise RuntimeError(answer)
    if status == 201:
        first.exec(SCHEMA)
    done = {int(r[0]["value"]) for r in first.exec("SELECT rowid FROM m")[0]["rows"]} \
        if status == 409 else set()

    # batches of whole melodies under MAX_BODY bytes
    batches, current, size = [], [], 0
    for d, p in melodies(db_dir):
        if d in done:
            continue
        stmt = {"sql": "INSERT INTO m (rowid, grams) VALUES (?, ?)",
                "params": [integer(d), text(tokens(p))]}
        n = len(stmt["params"][1]["value"]) + 120
        if current and size + n > MAX_BODY:
            batches.append(current)
            current, size = [], 0
        current.append(stmt)
        size += n
    if current:
        batches.append(current)
    print(f"{len(batches)} batches, {len(done)} melodies already uploaded", flush=True)

    clients = [Remote(name) for _ in range(jobs)]
    started, sql_us, sent = time.time(), 0, 0

    def send(k):
        return clients[k % jobs].batch(batches[k])

    with ThreadPoolExecutor(jobs) as pool:
        for us in pool.map(send, range(len(batches))):
            sql_us += us
            sent += 1
            if sent % 50 == 0 or sent == len(batches):
                print(f"  {sent}/{len(batches)} batches, {time.time() - started:.0f} s, "
                      f"SQL {sql_us / 1e6:.1f} s", flush=True)
    print(json.dumps({"upload_s": round(time.time() - started, 1), "sql_s": round(sql_us / 1e6, 1)}))
    try:
        _, us = first.exec("INSERT INTO m (m) VALUES ('optimize')")
        print(f"optimize: SQL {us / 1e6:.1f} s")
    except RuntimeError as e:
        print(f"optimize failed: {e}")
    print(json.dumps(first.call("GET", "/v1/databases")[1]))


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
    db = open_database(db_dir)
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
        t = time.perf_counter()
        answer, sql_us = remote.exec(COUNT, [text(phrase(q))])
        return (time.perf_counter() - t) * 1000, sql_us / 1000, int(answer["rows"][0][0]["value"])

    rows = []
    for label, q in queries(db_dir, per_length):
        sa_ms, files = timed(lambda: sa_count(q))
        fts_ms, fts_files = timed(lambda: con.execute(COUNT, (phrase(q),)).fetchone()[0])
        assert files == fts_files, (label, files, fts_files)
        fts10_ms, _ = timed(lambda: con.execute(FIRST_10, (phrase(q),)).fetchall())
        app_ms, _ = timed(lambda: db.search(q, approximate=False))
        row = dict(label=label, notes=len(q), files=files, sa_ms=sa_ms, fts_ms=fts_ms,
                   fts10_ms=fts10_ms, app_ms=app_ms)
        if remote:
            remote_count(q)  # first request: opens the connection, loads the pages
            results = [remote_count(q) for _ in range(3)]
            row["remote_sql_ms"] = min(r[1] for r in results)
            row["remote_ms"] = min(r[0] for r in results)
            assert results[0][2] == files, (label, "remote", results[0][2], files)
        rows.append(row)
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in row.items()}),
              flush=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(rows, indent=1))
    summary(rows)


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
    p = sub.add_parser("bench")
    p.add_argument("db_dir")
    p.add_argument("local")
    p.add_argument("name", nargs="?")
    p.add_argument("-n", "--per-length", type=int, default=30)
    p.add_argument("-o", "--out", default="data/bench/fts5.json")
    args = ap.parse_args()
    if args.command == "local":
        build_local(args.db_dir, args.out)
    elif args.command == "upload":
        upload(args.db_dir, args.name, args.jobs)
    else:
        bench(args.db_dir, args.local, args.name, args.per_length, args.out)


if __name__ == "__main__":
    main()
