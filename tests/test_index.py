import itertools
import random

import numpy as np
import pytest

from tests.conftest import write_midi
from whatthemidi.index import MODES, encode_query, mode_name
from whatthemidi.midi import parse_midi


def brute_force(melodies, query, octave, transpose):
    k = len(query)
    found = set()
    for name, pitches in melodies.items():
        for i in range(len(pitches) - k + 1):
            diff = [w - q for w, q in zip(pitches[i : i + k], query)]
            if not transpose:
                ok = all(d % 12 == 0 for d in diff) if octave else all(d == 0 for d in diff)
            elif octave:
                ok = all((d - diff[0]) % 12 == 0 for d in diff)
            else:
                ok = all(d == diff[0] for d in diff)
            if ok:
                found.add((name, i))
    return found


def found_by_index(db, query, octave, transpose):
    count, positions = db.find(query, octave, transpose)
    assert count == len(positions)
    docs = np.searchsorted(db.doc_starts, positions, side="right") - 1
    return {(db.names[d], int(p - db.doc_starts[d])) for d, p in zip(docs, positions)}


def queries(melodies, n=150):
    rng = random.Random(2)
    long = [p for p in melodies.values() if len(p) >= 8]
    for _ in range(n):
        pitches = rng.choice(long)
        k = rng.randint(2, 6)
        i = rng.randint(0, len(pitches) - k)
        shift = rng.choice([0, 0, 12, -12, 3, -5])
        yield [p + shift for p in pitches[i : i + k]]
    for _ in range(n):  # random queries, mostly without match
        yield [rng.randint(40, 90) for _ in range(rng.randint(2, 5))]


def test_db_skips_empty_and_broken_files(db, melodies):
    assert db.names == sorted(name for name, p in melodies.items() if p)
    assert db.meta["failed"] == 1
    assert db.meta["notes"] == sum(map(len, melodies.values()))


def test_arrays_are_memory_mapped(db):
    for mode in MODES:
        assert isinstance(db.texts[mode], np.memmap)
        assert isinstance(db.sas[mode], np.memmap)
        assert db.sas[mode].dtype == np.int32


@pytest.mark.parametrize("octave, transpose", list(itertools.product([False, True], repeat=2)))
def test_find_equals_brute_force(db, melodies, octave, transpose):
    matched = 0
    for query in queries(melodies):
        expected = brute_force(melodies, query, octave, transpose)
        assert found_by_index(db, query, octave, transpose) == expected, query
        matched += bool(expected)
    assert matched > 50  # the test is not trivial


def test_no_match_across_files(db, melodies):
    names = db.names
    for a, b in zip(names, names[1:]):
        query = melodies[a][-2:] + melodies[b][:2]
        if len(query) < 4:
            continue
        for octave, transpose in itertools.product([False, True], repeat=2):
            assert found_by_index(db, query, octave, transpose) == brute_force(
                melodies, query, octave, transpose
            )


def test_search_groups_by_file(db, melodies):
    query = [60, 62]
    result = db.search(query, limit=50)
    expected = brute_force(melodies, query, False, False)
    assert result["total"] == len(expected)
    assert result["files_total"] == len({name for name, _ in expected})
    assert result["files_exact"]
    occurrences = [r["occurrences"] for r in result["results"]]
    assert occurrences == sorted(occurrences, reverse=True)
    assert sum(occurrences) == len(expected)
    for r in result["results"]:
        positions = sorted(i for name, i in expected if name == r["file"])
        assert [p["pos"] for p in r["positions"]] == positions[:10]
        assert r["match"] == {"pos": positions[0], "len": 2}
        notes = [n["p"] for n in r["notes"]]
        pitches = melodies[r["file"]]
        assert notes == pitches[r["start"] : r["start"] + len(notes)]
        assert notes[positions[0] - r["start"] :][:2] == query


def test_search_limit(db):
    assert len(db.search([60, 62], limit=3)["results"]) == 3
    assert len(db.search([60, 62], limit=1000)["results"]) <= 50


def test_search_no_match(db):
    assert db.search([1, 2, 3]) == {"total": 0, "files_total": 0, "files_exact": True,
                                     "files_approximate": 0, "results": []}


def test_search_errors(db):
    with pytest.raises(ValueError):
        db.search([60], transpose=True)
    with pytest.raises(ValueError):
        db.search([])
    with pytest.raises(ValueError):
        db.search([60] * 65)


def test_context_bounds(db):
    n = db.doc_length(0)
    ctx = db.context(0, 0, 1, context=5)
    assert ctx["start"] == 0 and len(ctx["notes"]) == min(n, 6)
    with pytest.raises(ValueError):
        db.context(0, n, 1)
    with pytest.raises(ValueError):
        db.context(len(db.names), 0, 1)


def test_encode_query():
    q = [60, 64, 62, 74]
    assert list(encode_query(q, "exact")) == q
    assert list(encode_query(q, "octave")) == [0, 4, 2, 2]
    assert list(encode_query(q, "transpose")) == [127 + 4, 127 - 2, 127 + 12]
    assert list(encode_query(q, "transpose_octave")) == [4, 10, 0]
    assert mode_name(octave=True, transpose=True) == "transpose_octave"


def test_parse_midi_melody(tmp_path):
    path = tmp_path / "song.mid"
    write_midi(
        path,
        [
            (False, [(0, 60), (0, 64), (0, 67), (480, 70), (1440, 50)]),  # chord, then 2 notes
            (False, [(480, 72), (960, 65)]),  # higher note at 480
            (True, [(0, 90), (240, 36)]),  # drums are ignored
        ],
        tempos=((0, 120.0), (960, 60.0)),
    )
    m = parse_midi(path)
    assert m.ppq == 480
    assert list(m.pitches) == [67, 72, 65, 50]
    assert list(m.ticks) == [0, 480, 960, 1440]
    # 2 quarters at 120 BPM, then 1 quarter at 60 BPM
    assert np.allclose(m.seconds, [0, 0.5, 1.0, 2.0])


def test_skyline_drops_notes_under_a_held_note(tmp_path):
    from symusic import Note, Score, Tempo, Track


    def parse_midi(path):
        s = Score(str(path))
        notes = [(n.time, n.pitch, n.duration) for t in s.tracks for n in t.notes]
        t, p, d = zip(*notes)
        from whatthemidi.midi import melody_from_notes
        return melody_from_notes(t, p, [0], [500000], s.ticks_per_quarter, durations=d, lookback=16)

    score = Score(480)
    right, left = (Track(name=n, program=0, is_drum=False) for n in ("right", "left"))
    for i, pitch in enumerate([77, 69, 70, 72, 74]):  # F5 A4 A#4 C5 D5, quarter notes
        right.notes.append(Note(480 * i, 480, pitch, 90))
    left.notes.append(Note(480 * 3 + 240, 240, 65, 90))  # F4 while C5 sounds: dropped
    left.notes.append(Note(480 * 5, 480, 58, 90))  # A#3 after the last note: kept
    right.notes.append(Note(480 * 6 - 10, 480, 72, 90))  # C5, 10 ticks before A#3 ends
    left.notes.append(Note(480 * 6 + 5, 480, 60, 90))  # C4: C5 sounds, dropped
    score.tracks.extend([right, left])
    score.tempos.append(Tempo(0, 120.0))
    path = tmp_path / "sky.mid"
    score.dump_midi(str(path))
    assert list(parse_midi(path).pitches) == [77, 69, 70, 72, 74, 58, 72]

    # legato: a higher note that ends 10 ticks after the next onset does not drop it
    score = Score(480)
    track = Track(name="legato", program=0, is_drum=False)
    for i, pitch in enumerate([72, 69, 67]):
        track.notes.append(Note(480 * i, 490, pitch, 90))
    score.tracks.append(track)
    score.dump_midi(str(path))
    assert list(parse_midi(path).pitches) == [72, 69, 67]
