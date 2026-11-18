# bench: suffix array against SQLite FTS5

`fts5.py` indexes the same melodies with SQLite FTS5 and compares the
search time with the suffix array, locally and on
[sqlite4all.reduck.ai](https://sqlite4all.reduck.ai/) (hosted SQLite
over HTTP).

**Result:** keep the suffix array. On the full index, FTS5 is slower on
172 of 190 queries (13 times slower for the median query), and its worst
query takes 937 ms. On sqlite4all, the network takes 97 to 99 % of the
time of a search, also on a database of maximal size (1 GiB).

## Method

- **Tokens:** each melody is one row of a contentless FTS5 table
  (`content=''`, `columnsize=0`). It has one token for each 4
  consecutive pitches, in 8 hex digits: `3c3e4043` is C4 D4 E4 G4.
- **Queries:** a query of n notes is the phrase of its n - 3 tokens. A
  phrase matches only consecutive tokens, so it finds the same exact
  sequences as the suffix array.
- **Test set:** the 10 tunes of `collect/evaluate.py`, plus 30 random
  pieces each of 4, 6, 8, 12, 16 and 32 notes, taken from the indexed
  melodies. For each of the 190 queries, the engines must find the same
  number of files.
- **Measurement:** best of 3 runs, with the pages in the disk cache, on
  an Apple M4 Max. Only the exact mode is measured (no transposition).

| Column         | Measures                                                              |
| -------------- | --------------------------------------------------------------------- |
| suffix array   | `sa_search`, then the number of files with the sequence               |
| FTS5           | `SELECT count(*) FROM m WHERE m MATCH ?`                              |
| FTS5, first 10 | `SELECT rowid … LIMIT 10`: 10 files, not ranked                       |
| what-the-midi  | the search of the server: the 10 best files, ranked, with their notes |

## Lakh `clean_midi`: 13,598 melodies, 22.7 M notes

| Engine                                  | Median   | p90     | Max     |
| --------------------------------------- | -------- | ------- | ------- |
| suffix array                            | 0.011 ms | 0.01 ms | 0.4 ms  |
| FTS5, local                             | 0.051 ms | 0.12 ms | 0.2 ms  |
| FTS5 on sqlite4all, time in SQLite      | 0.26 ms  | 0.40 ms | 0.5 ms  |
| FTS5 on sqlite4all, time of the request | 38.6 ms  | 44.5 ms | 56.4 ms |

The upload took 82 s (900 requests of 240 KB, 23.8 s of SQLite time).
The remote database is 103 MB, the same as the local one.

## Full index: 4.19 M melodies, 1.74 G notes (local only)

| Engine         | Median   | p90     | Max    |
| -------------- | -------- | ------- | ------ |
| suffix array   | 0.015 ms | 0.57 ms | 53 ms  |
| FTS5           | 0.29 ms  | 6.6 ms  | 937 ms |
| FTS5, first 10 | 0.14 ms  | 1.9 ms  | 20 ms  |
| what-the-midi  | 0.12 ms  | 1.9 ms  | 15 ms  |

|       | Suffix array (exact mode) | FTS5                                              |
| ----- | ------------------------- | ------------------------------------------------- |
| Size  | 8.7 GB (5 bytes per note) | 6.1 GB (3.5 bytes per note)                       |
| Build | 1.6 min                   | 26 min (one thread, of which 2 min of `optimize`) |

The worst cases:

- **FTS5: long runs of one note.** 32 × C#7 takes 937 ms, against 51 ms
  for the suffix array. All the tokens of the phrase are the same
  frequent token, and FTS5 reads its positions in every file.
- **Suffix array: 4 frequent notes.** E5 E5 E5 E5 (122k files) takes
  53 ms, against 7 ms for FTS5. A 4-note query is one FTS5 token: FTS5
  reads 1 list of files, but the suffix array reads each occurrence to
  count the files. The search of the server reads at most 100k
  occurrences (15 ms).

## What FTS5 cannot do here

1. **Queries of 1 to 3 notes:** they are shorter than a token. They need
   prefix queries (`3c3e40*`).
2. **The occurrences in each file:** the result cards and the ranking use
   their number and positions. A contentless table gives only the files.
3. **Transposition and ignore octave:** each mode needs 1 more token
   column, so about 4 times the size.
4. **Seeds of the tolerant search:** the suffix array gives the number of
   hits of a 3 to 8-note piece without reading them. FTS5 reads the
   lists.

## One shard of maximal size on sqlite4all

`fts5.py shard` takes the first melodies of the full index until the
FTS5 file reaches 984 MiB (1 GiB minus a margin):

- 217,143 melodies, 265.8 M notes. The remote database `midi-shard0` is
  1,031,356,416 bytes, 96 % of the 1 GiB limit.
- 40 melodies of more than 27,700 notes are left out (on both sides).
  Their tokens do not fit in one request (256 KiB), and sqlite4all keeps
  at most 1 MiB in one value.
- The upload took 14 min (10,188 requests, 7.3 min of SQLite time).
- No `optimize`: on a 1 GiB database it would need a second copy of the
  index and more than the 5 s limit of a statement (29 s locally). The
  local copy is not optimized either, so both have the same segments.

| Engine | Median | p90 | Max |
| --- | --- | --- | --- |
| suffix array | 0.027 ms | 0.09 ms | 8.6 ms |
| FTS5, local | 1.07 ms | 2.6 ms | 12.8 ms |
| FTS5 on sqlite4all, time in SQLite | 1.06 ms | 4.0 ms | 7.0 ms |
| FTS5 on sqlite4all, time of the request | 33.0 ms | 36.2 ms | 43.6 ms |
| FTS5, local, after `optimize` | 0.089 ms | 0.44 ms | 9.9 ms |

- **FTS5 against the suffix array:** FTS5 is slower on 186 of 190
  queries (34 times slower for the median query).
- **Network:** it takes 97 % of the time of a remote request (median).
  On the server, SQLite is as fast as locally.
- **No `optimize`:** FTS5 is 12 times slower. The many segments of an
  index built by small transactions are each searched.

What the upload found on sqlite4all:

1. **`LOCAL_STORAGE_LIMIT`**, an error that the documentation does not
   list: after about 4,300 requests (459 MB), the database refused all
   writes for about 70 s, until the server had copied the writes to S3.
   `upload` now waits 15 s and sends the batch again.
2. **A contentless FTS5 table cannot be scanned** (`SELECT rowid FROM
   m`: "table does not support scanning"). Each batch now writes its
   number to the table `uploaded`, so an upload can continue where it
   stopped.

## The full index on sqlite4all

- With the shard above, a database holds about 265 M notes. The full
  index (1.74 G notes) needs 7 databases, and each search is 7 requests
  in parallel. The account limit was 10 GiB this afternoon; the API now
  shows 1 TB.
- The upload is about 15.5 GB of requests and 50 min of SQLite time,
  with pauses for `LOCAL_STORAGE_LIMIT`: about 1.5 to 2 hours.

## Commands

```sh
uv run collect/run.py build data/db-lakh --only lakh_clean
uv run bench/fts5.py local data/db-lakh data/fts5-lakh.sqlite
API_KEY=rsk_... uv run bench/fts5.py upload data/db-lakh midi-lakh
API_KEY=rsk_... uv run bench/fts5.py bench data/db-lakh data/fts5-lakh.sqlite midi-lakh -o data/bench/fts5-lakh.json
uv run bench/fts5.py local data/db-all data/fts5-all.sqlite   # 26 min
uv run bench/fts5.py bench data/db-all data/fts5-all.sqlite -o data/bench/fts5-all.json
uv run bench/fts5.py shard data/db-all data/shard     # 4 min
API_KEY=rsk_... uv run bench/fts5.py upload data/shard midi-shard0 -j 8 --no-optimize
API_KEY=rsk_... uv run bench/fts5.py bench data/shard data/shard/fts5.sqlite midi-shard0 -o data/bench/fts5-shard.json
```
