"""Approximate search: the query with extra notes between its notes, or
with a few notes missing.

The melody of a file is the highest note at each onset. In polyphonic
music, notes of other voices come between the notes that a listener
hears as the melody (a left hand note while the right hand holds a long
note). An exact search then finds nothing. This search:

1. cuts the query in seeds: short exact pieces (3 to 8 notes), each as
   short as possible but with at most SEED_CAP occurrences;
2. finds the seeds with the suffix array, and groups the hits by file (and
   by transposition in the "any key" modes);
3. aligns the query with the region around the hits (dynamic
   programming): at most MAX_INSERT extra notes between 2 query notes, at
   most MAX_SKIP query notes missing.

The quality of an alignment is (matched - missing - cost of the extra
notes) / query length: 1 for an exact match. An extra note below the
previous melody note costs less than one above it: it is what polyphony
makes (another voice plays under a held melody note).
"""

from dataclasses import dataclass, field

import numpy as np
from pydivsufsort import sa_search

SEED_MIN, SEED_MAX, SEED_CAP = 3, 8, 5000
MAX_INSERT = 3  # extra notes between 2 consecutive query notes
MAX_SKIP = 2  # query notes missing in a row (wrong or extra notes in the query)
# An extra note below the previous melody note is what polyphony makes
# (another voice under a held note): it costs less than a note above it.
INSERT_COST_BELOW, INSERT_COST_ABOVE = 0.1, 0.4
# ranking: an approximate match has the score of an exact match (log2 of
# matches and copies) minus these penalties (log2 units)
INSERT_PENALTY_BELOW = 0.2  # like 1.15 times fewer copies
INSERT_PENALTY_ABOVE = 1.0  # like 2 times fewer copies
SKIP_PENALTY = 4.0  # a missing note: like 16 times fewer copies
MIN_QUALITY = 0.75
MIN_NOTES = 6  # shorter queries: exact search only
MAX_REGIONS = 400  # regions aligned per search
NEG = -1e9


@dataclass
class Alignment:
    doc: int
    start: int  # position in the file of the first matched note
    notes: list = field(default_factory=list)  # matched positions in the file
    quality: float = 0.0
    inserted: int = 0
    skipped: int = 0
    inserted_above: int = 0  # extra notes above the previous melody note

    @property
    def penalty(self):
        return (INSERT_PENALTY_BELOW * (self.inserted - self.inserted_above)
                + INSERT_PENALTY_ABOVE * self.inserted_above + SKIP_PENALTY * self.skipped)


def _match_matrix(window, query, octave, transpose, shift):
    t = window.astype(np.int16)[None, :]
    q = np.asarray(query, dtype=np.int16)[:, None] + shift
    if octave:
        return (t - q) % 12 == 0
    return t == q


def _insert_costs(window):
    """costs[dj][j]: cost of the dj - 1 extra notes between a melody note at
    j - dj and the next one at j."""
    w = len(window)
    costs = [None, np.zeros(w)]
    for dj in range(2, MAX_INSERT + 2):
        c = np.zeros(w)
        if dj < w:
            for t in range(1, dj):
                below = window[t : w - dj + t] < window[: w - dj]
                c[dj:] += np.where(below, INSERT_COST_BELOW, INSERT_COST_ABOVE)
        costs.append(c)
    return costs


def align(window, query, octave=False, transpose=False, shift=0):
    """Best alignment of the query in window (pitches): (score, inserted,
    skipped, matched positions in window, extra notes above), or None."""
    window = np.asarray(window, dtype=np.int16)
    m, w = len(query), len(window)
    match = _match_matrix(window, query, octave, transpose, shift)
    costs = _insert_costs(window)
    dp = np.full((m, w), NEG)
    back = np.zeros((m, w, 2), dtype=np.int8)  # (query step, window step) to the previous match
    for i in range(m):
        best = np.where(match[i], 1.0 - i, NEG) if i <= MAX_SKIP else np.full(w, NEG)  # start here
        for di in range(1, min(MAX_SKIP + 1, i) + 1):
            prev = dp[i - di]
            for dj in range(1, MAX_INSERT + 2):
                if dj >= w:
                    break
                cand = np.full(w, NEG)
                cand[dj:] = prev[:-dj] + 1.0 - (di - 1) - costs[dj][dj:]
                better = match[i] & (cand > best)
                best = np.where(better, cand, best)
                back[i, better] = (di, dj)
        dp[i] = best
    # end: the last query notes can be missing
    end_scores = [(dp[i].max() - (m - 1 - i), i) for i in range(max(0, m - 1 - MAX_SKIP), m)]
    score, i = max(end_scores)
    if score <= NEG / 2:
        return None
    j = int(dp[i].argmax())
    matched, inserted, above, skipped = [j], 0, 0, m - 1 - i
    while True:
        di, dj = int(back[i, j, 0]), int(back[i, j, 1])
        if di == 0:  # start of the alignment
            skipped += i
            break
        skipped += di - 1
        inserted += dj - 1
        above += int((window[j - dj + 1 : j] >= window[j - dj]).sum())
        i, j = i - di, j - dj
        matched.append(j)
    return score, inserted, skipped, matched[::-1], above


@dataclass
class _Seed:
    query_pos: int
    length: int
    left: int
    count: int


def seeds(text, sa, pattern):
    """Exact pieces of the encoded query, each with at most SEED_CAP hits."""
    out = []
    for i in range(len(pattern) - SEED_MIN + 1):
        for length in range(SEED_MIN, min(SEED_MAX, len(pattern) - i) + 1):
            count, left = sa_search(text, sa, pattern[i : i + length].tobytes())
            if count == 0:
                break
            if count <= SEED_CAP:
                out.append(_Seed(i, length, left, count))
                break
    return out


def search(db, notes, octave=False, transpose=False):
    """Approximate matches of notes in one Database: {doc: [Alignment]}."""
    from .index import encode_query, mode_name  # import here: index imports this module

    m = len(notes)
    if m < MIN_NOTES:
        return {}
    mode = mode_name(octave, transpose)
    pattern = encode_query(notes, mode)
    text, sa, exact = db.texts[mode], db.sas[mode], db.texts["exact"]
    found = seeds(text, sa, pattern)
    if not found:
        return {}

    # hits: text position, query position, seed length
    x = np.concatenate([np.asarray(sa[s.left : s.left + s.count], dtype=np.int64) for s in found])
    qi = np.concatenate([np.full(s.count, s.query_pos) for s in found])
    ln = np.concatenate([np.full(s.count, s.length) for s in found])
    doc = np.searchsorted(db.doc_starts, x, side="right") - 1
    shift = (exact[x].astype(np.int64) - np.asarray(notes)[qi]) if transpose else np.zeros(len(x), np.int64)
    if transpose and octave:
        shift %= 12

    # regions: hits of one file (and one transposition) close to each other
    order = np.lexsort((x, shift, doc))
    x, qi, ln, doc, shift = x[order], qi[order], ln[order], doc[order], shift[order]
    span = m * (MAX_INSERT + 1)
    new_region = np.r_[True, (doc[1:] != doc[:-1]) | (shift[1:] != shift[:-1]) | (x[1:] - x[:-1] > span)]
    region = np.cumsum(new_region) - 1
    starts = np.flatnonzero(new_region)
    ends = np.r_[starts[1:], len(x)]

    # evidence: query notes covered by the seeds of the region
    def coverage(a, b):
        covered = np.zeros(m + 1, dtype=bool)
        for q0, n in zip(qi[a:b], ln[a:b]):
            covered[q0 : q0 + n + (1 if transpose else 0)] = True
        return covered.sum()

    candidates = sorted(((coverage(a, b), a, b) for a, b in zip(starts, ends)), reverse=True)
    results = {}
    for cov, a, b in candidates[:MAX_REGIONS]:
        if cov < m * 0.4:
            break
        d = int(doc[a])
        doc_start, doc_end = int(db.doc_starts[d]), int(db.doc_starts[d + 1]) - 1
        lo = max(doc_start, int(x[a] - qi[a] * (MAX_INSERT + 1)) - 1)
        hi = min(doc_end, int(x[b - 1] + (m - qi[b - 1]) * (MAX_INSERT + 1)) + 1)
        aligned = align(np.asarray(exact[lo:hi]), notes, octave, transpose, int(shift[a]))
        if aligned is None:
            continue
        score, inserted, skipped, matched, above = aligned
        quality = score / m
        if quality < MIN_QUALITY or (inserted == 0 and skipped == 0):
            continue  # too different, or an exact match (the exact search has it)
        positions = [lo + j - doc_start for j in matched]
        results.setdefault(d, []).append(
            Alignment(d, positions[0], positions, quality, inserted, skipped, above))
    return results
