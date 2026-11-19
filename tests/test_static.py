"""The static index (whatthemidi/static.py) read by web/src/lib/static-index.js
must find the same matches and files as a brute-force search."""

import json
import random
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from tests.conftest import write_midi
from whatthemidi import static
from whatthemidi.index import build as build_index
from whatthemidi.static import build, load_names

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@pytest.fixture(scope="module")
def two_parts(tmp_path_factory, melodies):
    """The same melodies in an index of 2 parts (part-000/, part-001/)."""
    root = tmp_path_factory.mktemp("parts")
    names = sorted(melodies)
    for k, half in enumerate((names[:15], names[15:])):
        for name in half:
            write_midi(root / f"midi-{k}" / name, [(False, [(240 * i, p) for i, p in enumerate(melodies[name])])])
        build_index(root / f"midi-{k}", root / "db" / f"part-{k:03d}", jobs=1, progress=False)
    return root / "db"


def check(out, rows, tmp_path):
    queries = tmp_path / "queries.json"
    queries.write_text(json.dumps(rows))
    run = subprocess.run(["node", "bench/static.mjs", "--dir", str(out), "--run", "check",
                          "--queries", str(queries)], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


def brute_force(melodies, query):
    """(matches, files) of an exact query."""
    k, matches, files = len(query), 0, 0
    for pitches in melodies.values():
        n = sum(pitches[i : i + k] == query for i in range(len(pitches) - k + 1))
        matches += n
        files += n > 0
    return matches, files


def expected(melodies):
    rng = random.Random(3)
    long = [p for p in melodies.values() if len(p) >= 8]
    qs = []
    for _ in range(150):
        p = rng.choice(long)
        k = rng.randint(1, min(len(p), 40))
        i = rng.randint(0, len(p) - k)
        qs.append(p[i : i + k])
    qs += [[rng.choice([48, 60, 62, 64, 67, 72]) for _ in range(rng.randint(1, 6))] for _ in range(80)]
    qs += [[60] * k for k in (1, 2, 3, 8, 64)] + [[0], [127], [61, 61]]
    return [dict(zip(("matches", "files"), brute_force(melodies, q)), notes=q) for q in qs]


@pytest.mark.parametrize("leaf, fanout, key_min, part_bytes", [
    (4, 3, 0, 64),  # many levels, many reads between 2 leaves, many parts
    (16, 4, 5, 1 << 20),
    (4096, 256, 0, 64 << 20),  # the default: one leaf
])
def test_reader_equals_brute_force(tmp_path, db_dir, melodies, leaf, fanout, key_min, part_bytes):
    out = tmp_path / "static"
    build(db_dir, out, leaf=leaf, fanout=fanout, key_min=key_min, part_bytes=part_bytes, progress=False)
    rows = expected(melodies)
    assert sum(r["matches"] > 0 for r in rows) > 100
    check(out, rows, tmp_path)


def test_two_parts_and_groups(tmp_path, two_parts, melodies, monkeypatch):
    """An index of 2 parts gives one static index of all the files, also
    when the dup pairs are sorted in many groups of files."""
    monkeypatch.setattr(static, "GROUP", 100)
    out = tmp_path / "static"
    stats = build(two_parts, out, leaf=16, fanout=4, progress=False)
    assert stats["notes"] == sum(len(p) + 1 for p in melodies.values() if p)
    check(out, expected(melodies), tmp_path)


@pytest.mark.parametrize("source", ["db_dir", "two_parts"])
def test_files_and_text(tmp_path, request, melodies, source):
    db_dir = request.getfixturevalue(source)
    out = tmp_path / "static"
    names, titles = load_names(db_dir)
    build(db_dir, out, leaf=8, fanout=4, part_bytes=128, names=names, titles=titles,
          files_chunk_notes=37, progress=False)
    query = next(p for p in melodies.values() if len(p) >= 30)[10:14]
    script = f"""
        import {{ StaticIndex, httpStore }} from './web/src/lib/static-index.js';
        import {{ readFile, open }} from 'node:fs/promises';
        const dir = {json.dumps(str(out))};
        const store = {{
          get: async (k) => new Uint8Array(await readFile(dir + '/' + k)),
          getRange: async (k, off, n) => {{
            const f = await open(dir + '/' + k); const b = new Uint8Array(n);
            await f.read(b, 0, n, off); await f.close(); return b;
          }},
        }};
        const ix = await StaticIndex.open(store);
        const r = await ix.search({json.dumps(query)}, {{ limit: 1000 }});
        const files = await ix.files(r.positions);
        const texts = await Promise.all(r.positions.map((p) => ix.text(p, {len(query)})));
        console.log(JSON.stringify({{ files, texts: texts.map((t) => [...t]) }}));
    """
    run = subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout)
    found = {(f["name"], f["offset"]) for f in got["files"]}
    want = {(name, i) for name, p in melodies.items() for i in range(len(p) - 3) if p[i : i + 4] == query}
    assert found == want
    assert all(t == query for t in got["texts"])
    assert all(f["end"] - f["start"] == len(melodies[f["name"]]) for f in got["files"])
    doc_starts = static.load_source(db_dir)[2]
    assert all(f["start"] in doc_starts for f in got["files"])
