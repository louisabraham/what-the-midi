"""The SQLite database of the demo: melodies in an FTS5 table of 4-note
tokens, and the name, title and copies of each file. Rows are numbered by
copies, most copied first: FTS5 returns the matches in row order, so the
first rows of a search are the best-known files.

    uv run demo/sqlite-s3/build_db.py data/db-all data/demo-all.sqlite                      # every melody
    uv run demo/sqlite-s3/build_db.py data/db-all data/shard/demo.sqlite --shard data/shard # one shard
    sqlite-convert -db <file> -to s3://<bucket> -region <r> -id <id> -preset reader
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from fts5 import SCHEMA, parts, tokens  # noqa: E402

BATCH = 20_000  # rows per transaction: fewer, larger segments before optimize


def main(db_dir: Path, out: Path, shard: Path | None):
    dirs = [d for d, _ in parts(db_dir)]
    names = [json.loads((d / "names.json").read_text()) for d in dirs]
    titles = [json.loads((d / "titles.json").read_text()) for d in dirs]
    starts = [np.load(d / "doc_starts.npy") for d in dirs]
    texts = [np.load(d / "text_exact.npy", mmap_mode="r") for d in dirs]
    copies_of = [np.load(d / "doc_copies.npy") for d in dirs]
    if shard:  # the docs of the shard, all in the first part
        doc = np.load(shard / "docs.npy").astype(np.int64)
        part = np.zeros(len(doc), dtype=np.int8)
    else:
        doc = np.concatenate([np.arange(len(s) - 1) for s in starts])
        part = np.concatenate([np.full(len(s) - 1, p, dtype=np.int8) for p, s in enumerate(starts)])
    copies = np.array([copies_of[p][d] for p, d in zip(part.tolist(), doc.tolist())], dtype=np.int64)
    order = np.argsort(-copies, kind="stable")  # row r + 1 is melody order[r]
    print(f"{len(order):,} melodies in {len(dirs)} parts", flush=True)

    out.unlink(missing_ok=True)
    con = sqlite3.connect(out, isolation_level=None)
    con.execute("PRAGMA journal_mode = OFF")
    con.execute(SCHEMA)
    con.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, name TEXT NOT NULL, title TEXT, copies INTEGER NOT NULL)")
    started = time.time()
    for a in range(0, len(order), BATCH):
        con.execute("BEGIN")
        for r in range(a, min(a + BATCH, len(order))):
            k = int(order[r])
            p, d = int(part[k]), int(doc[k])
            s = starts[p]
            con.execute("INSERT INTO m (rowid, grams) VALUES (?, ?)", (r + 1, tokens(texts[p][s[d]: s[d + 1] - 1])))
            con.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (r + 1, names[p][d], titles[p][d], int(copies[k])))
        con.execute("COMMIT")
        done = min(a + BATCH, len(order))
        if done % 200_000 < BATCH or done == len(order):
            print(f"  {done:,} / {len(order):,} rows, {time.time() - started:.0f} s", flush=True)
    t = time.time()
    con.execute("INSERT INTO m (m) VALUES ('optimize')")
    con.execute("VACUUM")
    con.close()
    print(json.dumps({"rows": len(order), "insert_s": round(t - started), "optimize_s": round(time.time() - t),
                      "mb": round(out.stat().st_size / 1e6)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("db_dir", type=Path, help="the index (what-the-midi index or collect/run.py build)")
    ap.add_argument("out", type=Path)
    ap.add_argument("--shard", type=Path, help="only the melodies of this shard (bench/fts5.py shard)")
    args = ap.parse_args()
    main(args.db_dir, args.out, args.shard)
