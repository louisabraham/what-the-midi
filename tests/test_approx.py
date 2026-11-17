import sys
from pathlib import Path

import numpy as np
import pytest

from whatthemidi import approx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "collect"))
from neardup import leaders, signatures, similarity  # noqa: E402


def test_align_extra_notes():
    window = np.array([60, 62, 50, 64, 65, 40, 67])
    score, inserted, skipped, matched, above = approx.align(window, [60, 62, 64, 65, 67])
    assert (inserted, skipped, matched, above) == (2, 0, [0, 1, 3, 4, 6], 0)
    assert score == pytest.approx(5 - 2 * approx.INSERT_COST_BELOW)  # 50 and 40: under the melody
    *_, above = approx.align(np.array([60, 62, 70, 64]), [60, 62, 64])
    assert above == 1


def test_align_wrong_note():
    window = np.array([60, 62, 64, 65, 67, 69])
    score, inserted, skipped, matched, _ = approx.align(window, [60, 62, 63, 65, 67, 69])
    assert skipped == 1 and matched == [0, 1, 3, 4, 5]


def test_align_transposed_and_octave():
    window = np.array([65, 67, 56, 69, 70])  # [60, 62, 64, 65] + 5, with an extra note
    *_, matched, _ = approx.align(window, [60, 62, 64, 65], transpose=True, shift=5)
    assert matched == [0, 1, 3, 4]
    *_, matched, _ = approx.align(window, [53, 55, 57, 58], octave=True)  # one octave lower
    assert matched == [0, 1, 3, 4]


def test_approximate_search_finds_inserted_notes(db, melodies):
    name = next(n for n, p in melodies.items() if len(p) >= 30)
    melody = melodies[name]
    query = melody[10:12] + melody[13:21]  # the file has one more note than the query
    doc = db.names.index(name)
    found = approx.search(db, query)
    assert doc in found
    best = max(found[doc], key=lambda a: a.quality)
    assert best.start <= 10 and best.inserted + best.skipped >= 1 and best.quality >= approx.MIN_QUALITY


def test_signatures_estimate_jaccard():
    rng = np.random.default_rng(0)
    a = rng.integers(40, 90, 2000).astype(np.uint8)
    b = a.copy()
    b[rng.choice(2000, 40, replace=False)] = rng.integers(40, 90, 40)  # 2% of the notes changed
    c = rng.integers(40, 90, 2000).astype(np.uint8)
    sig = signatures(np.concatenate([a, b, c]), np.array([0, 2000, 4000, 6000]))
    assert similarity(sig[[0]], sig[[1]])[0] > 0.6
    assert similarity(sig[[0]], sig[[2]])[0] < 0.1


def test_leaders_do_not_chain():
    # 0 ~ 1 and 1 ~ 2, but not 0 ~ 2: 2 does not join the group of 0
    pairs = np.array([[0, 1], [1, 2], [3, 4]])
    priority = np.array([5.0, 4.0, 3.0, 1.0, 2.0])
    assert leaders(priority, pairs).tolist() == [0, 0, 2, 4, 4]
