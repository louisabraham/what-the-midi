"""The SQLite database of the demo: the melodies of a shard (bench/fts5.py
shard) in an FTS5 table of 4-note tokens, and the name, title and copies of
each file. Rows are numbered by copies, most copied first: FTS5 returns the
matches in row order, so the first rows of a search are the best-known
files.

    uv run demo/sqlite-s3/build_db.py data/shard data/db-all data/shard/demo.sqlite
    sqlite-convert -db data/shard/demo.sqlite -to s3://<bucket> -region <r> -id <id> -preset reader
"""

import json
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "bench"))
from fts5 import SCHEMA, tokens  # noqa: E402

BATCH = 20_000  # rows per transaction: fewer, larger segments before optimize


def main(shard: Path, db_dir: Path, out: Path):
    docs = np.load(shard / "docs.npy")  # index doc of each shard row
    starts = np.load(shard / "doc_starts.npy")
    text = np.load(shard / "text_exact.npy", mmap_mode="r")
    part = db_dir / "part-000"
    names = json.loads((part / "names.json").read_text())
    titles = json.loads((part / "titles.json").read_text())
    copies = np.load(part / "doc_copies.npy")[docs]
    order = np.argsort(-copies, kind="stable")  # new row r + 1 is shard row order[r]

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
            d = int(docs[k])
            con.execute("INSERT INTO m (rowid, grams) VALUES (?, ?)", (r + 1, tokens(text[starts[k]: starts[k + 1] - 1])))
            con.execute("INSERT INTO files VALUES (?, ?, ?, ?)", (r + 1, names[d], titles[d], int(copies[k])))
        con.execute("COMMIT")
        print(f"  {min(a + BATCH, len(order)):,} / {len(order):,} rows, {time.time() - started:.0f} s", flush=True)
    t = time.time()
    con.execute("INSERT INTO m (m) VALUES ('optimize')")
    con.execute("VACUUM")
    con.close()
    print(json.dumps({"rows": len(order), "insert_s": round(t - started), "optimize_s": round(time.time() - t),
                      "mb": round(out.stat().st_size / 1e6)}))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
