import random

import pytest
from symusic import Note, Score, Tempo, Track

from whatthemidi.index import Database, build


def write_midi(path, tracks, ppq=480, tempos=((0, 120.0),)):
    """tracks: list of (is_drum, [(time, pitch), ...])"""
    score = Score(ppq)
    for is_drum, notes in tracks:
        track = Track(name="drums" if is_drum else "melody", program=0, is_drum=is_drum)
        for time, pitch in notes:
            track.notes.append(Note(time, 240, pitch, 90))
        score.tracks.append(track)
    for time, qpm in tempos:
        score.tempos.append(Tempo(time, qpm))
    path.parent.mkdir(parents=True, exist_ok=True)
    score.dump_midi(str(path))


@pytest.fixture(scope="session")
def melodies():
    """Random monophonic melodies, with a small set of pitches so that
    searches find many matches. Some are empty: build() skips them."""
    rng = random.Random(1)
    out = {}
    for i in range(40):
        n = rng.choice([0, 1, 2, 5, 30, 30, 80])
        base = rng.choice([48, 60, 60, 60, 72])
        out[f"artist{i % 4}/song{i:02d}.mid"] = [
            base + rng.choice([0, 2, 4, 5, 7, 12]) for _ in range(n)
        ]
    return out


@pytest.fixture(scope="session")
def db_dir(tmp_path_factory, melodies):
    midi_dir = tmp_path_factory.mktemp("midi")
    for name, pitches in melodies.items():
        write_midi(midi_dir / name, [(False, [(240 * i, p) for i, p in enumerate(pitches)])])
    (midi_dir / "broken.mid").write_bytes(b"MThd not a midi file")
    db_dir = tmp_path_factory.mktemp("db")
    build(midi_dir, db_dir, jobs=2, progress=False)
    return db_dir


@pytest.fixture(scope="session")
def db(db_dir):
    return Database(db_dir)
