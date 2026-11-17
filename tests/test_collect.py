import io
import sys
import zipfile
from pathlib import Path

import pytest

from tests.conftest import write_midi
from whatthemidi.index import Database, MultiDatabase, open_database

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collect"))
from build import build  # noqa: E402
from ingest import connect, ingest  # noqa: E402

X = [60, 62, 64, 65, 67, 69, 71, 72, 74]
Y = [72, 71, 69, 67, 65, 64, 62, 60, 59]
Z = [50, 52, 50, 52, 53, 55, 57, 59, 60]


def midi_bytes(tmp_path, pitches, step=240):
    path = tmp_path / "tmp.mid"
    write_midi(path, [(False, [(step * i, p) for i, p in enumerate(pitches)])])
    return path.read_bytes()


@pytest.fixture
def store(tmp_path):
    a = midi_bytes(tmp_path, X)
    nested = io.BytesIO()
    with zipfile.ZipFile(nested, "w") as z:
        z.writestr("deep/z.mid", midi_bytes(tmp_path, Z))
    archive = tmp_path / "midiman.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("pop/a very long name for x.mid", a)
        z.writestr("other/copy of a.mid", a)  # same bytes
        z.writestr("other/x slower.mid", midi_bytes(tmp_path, X, step=480))  # same melody
        z.writestr("y.mid", midi_bytes(tmp_path, Y))
        z.writestr("short.mid", midi_bytes(tmp_path, X[:3]))
        z.writestr("broken.mid", b"MThd\x00\x00\x00\x06garbage")
        z.writestr("readme.txt", b"not a midi file")
        z.writestr("more.zip", nested.getvalue())
    clean = tmp_path / "clean"
    write_midi(clean / "Artist" / "Song X.mid", [(False, [(120 * i, p) for i, p in enumerate(X)])])

    store = tmp_path / "store"
    stats = ingest("midiman", [archive], store, jobs=1)
    assert stats == dict(files=7, dup_bytes=1, dup_melody=1, short=1, errors=1, melodies=3,
                         notes=27)
    # a source with a better priority, ingested later
    stats = ingest("lakh_clean", [clean], store, jobs=1)
    assert stats["dup_melody"] == 1 and stats["melodies"] == 0
    return store


def test_catalog(store):
    db = connect(store)
    statuses = dict(db.execute("SELECT path, status FROM files WHERE source = 'midiman'"))
    assert statuses["more/deep/z.mid"] == "ok"
    assert statuses["broken.mid"] == "error"
    assert db.execute("SELECT path FROM aliases").fetchall() == [("other/copy of a.mid",)]


def test_build(store, tmp_path):
    meta = build(store, tmp_path / "db")
    assert meta["files"] == 3
    db = Database(tmp_path / "db")
    docs = {name: i for i, name in enumerate(db.names)}
    # X has the name of the source with the best priority, without its top directory
    assert set(docs) == {"Song X.mid", "y.mid", "more/deep/z.mid"}
    assert db.source(docs["Song X.mid"]) == "lakh_clean"
    assert db.source(docs["y.mid"]) == "midiman"
    # 4 files have the melody X: 2 with the same bytes, 1 slower, 1 in lakh_clean
    assert db.copies[docs["Song X.mid"]] == 4
    result = db.search(X[2:6])
    assert result["results"][0]["file"] == "Song X.mid"
    assert result["results"][0]["copies"] == 4


def test_build_in_parts(store, tmp_path):
    build(store, tmp_path / "one")
    meta = build(store, tmp_path / "parts", part_notes=12)  # one melody of 9 notes per part
    assert meta["parts"] == 3
    one, parts = open_database(tmp_path / "one"), open_database(tmp_path / "parts")
    assert isinstance(parts, MultiDatabase) and len(parts.names) == 3
    for query, transpose in [(X[2:6], False), (Y[:3], False), ([0, 2], True), ([1, 1], True)]:
        a, b = one.search(query, transpose=transpose), parts.search(query, transpose=transpose)
        for r in a["results"] + b["results"]:
            del r["doc"]  # numbers differ: the parts keep the order of the store
        assert a == b, query
    doc = [parts.names[i] for i in range(3)].index("Song X.mid")
    assert parts.source(doc) == "lakh_clean"
    assert parts.context(doc, 0, 2, 1)["notes"][0]["p"] == X[0]


def test_note_budget(tmp_path):
    folder = tmp_path / "src"
    for i, pitches in enumerate([X, Y, Z]):
        write_midi(folder / f"{i}.mid", [(False, [(240 * j, p) for j, p in enumerate(pitches)])])
    store = tmp_path / "store"
    assert ingest("midiman", [folder], store, jobs=1, max_notes=0) is None
    stats = ingest("midiman", [folder], store, jobs=1, max_notes=10)
    assert stats["truncated"] and stats["notes"] >= 10


def test_titles_name_checksum_files(tmp_path):
    from symusic import Note, Score, Tempo, Track

    def song(title, pitches):
        score = Score(480)
        score.tracks.append(Track(name=title, program=0, is_drum=False))  # track 0 name
        for i, p in enumerate(pitches):
            score.tracks[0].notes.append(Note(240 * i, 240, p, 90))
        score.tempos.append(Tempo(0, 120.0))
        path = tmp_path / "tmp.mid"
        score.dump_midi(str(path))
        return path.read_bytes()

    archive = tmp_path / "lakh_full.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("a/0123456789abcdef0123456789abcdef.mid", song("AC/DC - Back in Black", X))
        z.writestr("b/fedcba9876543210fedcba9876543210.mid", song("Piano", Y))  # generic: no title
    store = tmp_path / "store"
    ingest("lakh_full", [archive], store, jobs=1)
    build(store, tmp_path / "db")
    db = Database(tmp_path / "db")
    # "b/" is the directory of all the other files of the source: it is removed
    assert sorted(db.names) == ["AC∕DC - Back in Black", "fedcba9876543210fedcba9876543210.mid"]
    assert db.search(X[:4])["results"][0]["title"] == "AC/DC - Back in Black"
