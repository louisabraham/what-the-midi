"""Groups the melodies that are almost the same (near duplicates).

Two files of the same piece often differ by a few notes: another
sequencing, a fixed wrong note, a different ending. Their melodies are
then not identical, and ingest.py keeps both. This step groups melodies
that share at least THRESHOLD of their 8-note sequences (Jaccard
similarity of the sets of 8-grams of pitches) and have similar lengths
(MIN_LENGTH_RATIO), and build.py keeps one melody per group.

Method, for 4M+ melodies:
1. One-permutation MinHash: each 8-gram is hashed once; the top 6 bits
   choose one of 64 bins, the bin keeps the smallest value. Two melodies
   have the same value in a bin with a probability close to their Jaccard
   similarity.
2. Locality-sensitive hashing: 16 bands of 4 bins. Melodies with the same
   4 values in a band are candidates.
3. Each candidate pair is checked with the 64 bins, and the pairs above
   THRESHOLD are joined with union-find.

Transposed copies are not grouped: an exact search in one key must still
find the file in that key.
"""

import sys
import time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

K = 8  # notes per sequence
BINS = 64
BANDS, ROWS = 16, 4
THRESHOLD = 0.5
MIN_LENGTH_RATIO = 0.75  # a part of a piece (one half, one movement) is not a copy
EMPTY = np.uint32(0xFFFFFFFF)
NEIGHBORS = 16  # in a bucket, each member is checked against this many others


def _mix(x):
    """splitmix64: spreads the bits of the 8-gram codes."""
    x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return x ^ (x >> np.uint64(31))


def signatures(pitches: np.ndarray, offsets: np.ndarray) -> np.ndarray:
    """(number of melodies, BINS) uint32 MinHash signatures of the
    melodies pitches[offsets[i]:offsets[i+1]]. EMPTY marks an empty bin."""
    m = len(offsets) - 1
    sig = np.full(m * BINS, EMPTY, dtype=np.uint32)
    n = len(pitches) - K + 1
    if n <= 0:
        return sig.reshape(m, BINS)
    p = pitches.astype(np.uint64)
    code = np.zeros(n, dtype=np.uint64)
    for j in range(K):  # 7 bits per pitch: the code of 8 pitches is exact
        code = (code << np.uint64(7)) | p[j : j + n]
    start = np.arange(n, dtype=np.int64)
    melody = np.searchsorted(offsets, start, side="right") - 1
    inside = start + K <= offsets[melody + 1]  # the 8 notes are in one melody
    x = _mix(code[inside])
    melody = melody[inside]
    bins = (x >> np.uint64(64 - 6)).astype(np.int64)
    values = (x & np.uint64(0xFFFFFFFF)).astype(np.uint32)
    np.minimum.at(sig, melody * BINS + bins, values)
    return sig.reshape(m, BINS)


def _shard_signatures(path):
    with np.load(path) as z:
        offsets = z["offsets"]
        return str(path), z["keys"], signatures(z["pitches"], offsets), np.diff(offsets)


def similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Estimated Jaccard similarity of the rows of 2 signature arrays."""
    full = (a != EMPTY) | (b != EMPTY)
    same = (a == b) & (a != EMPTY)
    return same.sum(axis=1) / np.maximum(full.sum(axis=1), 1)


def candidate_pairs(sig: np.ndarray):
    """Pairs (i, j), i < j, of melodies with the same 4 values in at least
    one band. In a bucket, each member is paired with the first member and
    with the member before it: union-find then joins a whole bucket of
    similar melodies, without checking all the pairs of a large bucket."""
    n = len(sig)
    codes = []
    for band in range(BANDS):
        cols = np.ascontiguousarray(sig[:, band * ROWS : (band + 1) * ROWS])
        ok = np.flatnonzero((cols != EMPTY).all(axis=1))
        if len(ok) < 2:
            continue
        halves = cols[ok].view(np.uint64)  # 4 uint32 -> 2 uint64
        key = _mix(halves[:, 0] ^ _mix(halves[:, 1]))
        order = np.argsort(key, kind="stable")
        key, ok = key[order], ok[order]
        same_as_previous = np.r_[False, key[1:] == key[:-1]]
        run_start = np.maximum.accumulate(np.where(same_as_previous, 0, np.arange(len(key))))
        members = np.flatnonzero(same_as_previous)
        for a, b in ((ok[members - 1], ok[members]), (ok[run_start[members]], ok[members])):
            lo, hi = np.minimum(a, b), np.maximum(a, b)
            codes.append(lo.astype(np.int64) * n + hi)
    if not codes:
        return np.zeros((0, 2), dtype=np.int64)
    codes = np.unique(np.concatenate(codes))
    return np.stack([codes // n, codes % n], axis=1)


def leaders(priority, pairs):
    """Group of each melody, without chains of similar melodies (A like B,
    B like C, but A not like C). Melodies are taken by decreasing
    priority; a melody that no group has yet starts a group, and takes the
    melodies directly similar to it that no group has yet.

    Returns the index of the first melody (the leader) of each group."""
    n = len(priority)
    rank = np.empty(n, dtype=np.int64)
    rank[np.argsort(-priority, kind="stable")] = np.arange(n)
    a, b = pairs[:, 0], pairs[:, 1]
    swap = rank[a] > rank[b]
    a, b = np.where(swap, b, a), np.where(swap, a, b)  # a: the better leader
    order = np.lexsort((rank[b], rank[a]))
    leader = np.full(n, -1, dtype=np.int64)
    for i, j in zip(a[order].tolist(), b[order].tolist()):
        if leader[i] == -1:
            leader[i] = i
        if leader[i] == i and leader[j] == -1:
            leader[j] = i
    free = leader == -1
    leader[free] = np.flatnonzero(free)
    return leader


def group(store: Path, sources, jobs=None):
    """Writes store/neardup.npz: for each melody hash, the hash of the
    first melody of its group. Returns statistics."""
    started = time.time()
    shards = [p for s in sources for p in sorted((store / "melodies").glob(f"{s}-*.npz"))]
    with Pool(jobs) as pool:
        results = pool.map(_shard_signatures, shards, chunksize=1)
    keys = np.concatenate([k for _, k, _, _ in results])
    sig = np.concatenate([s for _, _, s, _ in results])
    lengths = np.concatenate([n for _, _, _, n in results])
    hashed = time.time()
    print(f"{len(keys):,} melodies, signatures in {hashed - started:.0f} s", flush=True)

    pairs = candidate_pairs(sig)
    print(f"{len(pairs):,} candidate pairs in {time.time() - hashed:.0f} s", flush=True)
    step = 5_000_000
    sims = np.concatenate([similarity(sig[pairs[a:a + step, 0]], sig[pairs[a:a + step, 1]])
                           for a in range(0, len(pairs), step)]) if len(pairs) else np.zeros(0)
    lengths_a, lengths_b = lengths[pairs[:, 0]], lengths[pairs[:, 1]]
    ratio = np.minimum(lengths_a, lengths_b) / np.maximum(np.maximum(lengths_a, lengths_b), 1)
    accepted = pairs[(sims >= THRESHOLD) & (ratio >= MIN_LENGTH_RATIO)]
    print(f"{len(accepted):,} similar pairs at {time.time() - started:.0f} s", flush=True)

    # leaders: the melodies with the most files, then the longest
    from ingest import connect

    files = dict(connect(store).execute("""
        SELECT melody, COUNT(*) FROM (
            SELECT melody FROM files WHERE melody IS NOT NULL
            UNION ALL
            SELECT f.melody FROM aliases a JOIN files f ON a.md5 = f.md5 WHERE f.melody IS NOT NULL)
        GROUP BY melody"""))
    priority = np.array([files.get(k, 1) for k in keys.tolist()], dtype=np.float64)
    priority += lengths / (lengths.max() + 1)  # ties: the longest
    group_of = leaders(priority, accepted)
    np.savez(store / "neardup.npz", keys=keys, group=keys[group_of])
    _, sizes = np.unique(group_of, return_counts=True)
    return {
        "melodies": len(keys),
        "groups": len(sizes),
        "melodies_in_groups_of_2_or_more": int(sizes[sizes > 1].sum()),
        "largest_group": int(sizes.max()) if len(sizes) else 0,
        "seconds": round(time.time() - started, 1),
    }


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from sources import SOURCES

    print(group(Path(sys.argv[1]), [s.name for s in SOURCES]))
