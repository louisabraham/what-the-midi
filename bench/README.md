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

| Engine                                  | Median   | p90     | Max     |
| --------------------------------------- | -------- | ------- | ------- |
| suffix array                            | 0.027 ms | 0.09 ms | 8.6 ms  |
| FTS5, local                             | 1.07 ms  | 2.6 ms  | 12.8 ms |
| FTS5 on sqlite4all, time in SQLite      | 1.06 ms  | 4.0 ms  | 7.0 ms  |
| FTS5 on sqlite4all, time of the request | 33.0 ms  | 36.2 ms | 43.6 ms |
| FTS5, local, after `optimize`           | 0.089 ms | 0.44 ms | 9.9 ms  |

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

## The shard read from S3, with no server

The reader of reduck-sqlite (`reduck-sqlite/reader`: SQLite in
WebAssembly) reads `midi-shard0` straight from its objects, through
CloudFront. The service stores it in 1,003 chunks of 1 MiB (251,796
pages). `open` takes 0.4 to 0.5 s: 9 GETs, 3.7 MB (the interior pages of
the B-trees and the hot chunks). `bench/reader.mjs` then runs the 190
queries one after the other, with the 256 MiB cache of the reader:
"cold" is the first run of a query (it GETs the chunks it needs, and
the cache keeps those of the queries before), "warm" the best of 3 runs
after it.

|                                   | Median       | p90    | Max    |
| --------------------------------- | ------------ | ------ | ------ |
| cold, read-ahead 16 (the default) | 6.3 s        | 12.4 s | 17.4 s |
| cold, read-ahead 0                | 1.0 s        | 3.9 s  | 4.7 s  |
| warm (the chunks are in memory)   | 0.37 ms      | 1.5 ms | 7.6 ms |
| GETs, read-ahead 16               | 239 (206 MB) | 460    | 646    |
| GETs, read-ahead 0                | 20 (17 MB)   | 76     | 93     |

Chunks and pages that each query reads (counted by wrapping the
reader's `page()`, median):

| Queries  | Chunks read  | Pages read    | GETs, read-ahead 0 | Cold   | Warm    |
| -------- | ------------ | ------------- | ------------------ | ------ | ------- |
| 10 tunes | 56           | 167           | 29                 | 1.5 s  | 0.88 ms |
| 4 notes  | 15           | 22            | 3                  | 0.15 s | 0.06 ms |
| 8 notes  | 48           | 104           | 16                 | 0.84 s | 0.21 ms |
| 16 notes | 94           | 256           | 42                 | 2.1 s  | 0.58 ms |
| 32 notes | 159          | 540           | 78                 | 4.1 s  | 1.5 ms  |
| all 190  | 53 (max 176) | 134 (max 599) | 20                 | 1.0 s  | 0.37 ms |

- **Scattered pages:** a query reads about 2.5 pages of each chunk it
  needs (1 % of the 1 MiB it fetches). Each token of the phrase is looked
  up in every FTS5 segment, and the index has many segments because it
  was never optimized. Even a 4-note query (1 token) reads 15 chunks.
- **One round trip per missing chunk:** SQLite learns the next page only
  after it reads the one before, so the reader runs the statement again
  after each GET: 51 ms per GET from the client.
- **The read-ahead costs 6 times the time and 9 times the bytes here.**
  Its size doubles at each wait in a B-tree, also inside one statement,
  and its counter is never reset. An FTS5 lookup jumps between segments
  far apart in the same B-tree (`m_data`), so the chunks it fetches ahead
  are rarely read. With the default, the 190 queries downloaded 41.4 GB
  in 21.6 min; without read-ahead, 4.7 GB in 4.6 min.
- **Warm**, the reader answers in 0.37 ms: 90 times faster than a
  request to the sqlite4all API (33 ms), but 14 times slower than the
  suffix array (0.027 ms).

### Ways to make the reader faster

Measured on the same 190 queries (median cold query; the first line is
the default):

| Reader setting                                                    | Median | 32 notes | Downloaded | 190 queries  |
| ----------------------------------------------------------------- | ------ | -------- | ---------- | ------------ |
| read-ahead 16, cache 256 MiB                                      | 6.3 s  | 13.0 s   | 41.4 GB    | 21.6 min     |
| read-ahead 0, cache 256 MiB                                       | 1.0 s  | 4.1 s    | 4.7 GB     | 4.6 min      |
| read-ahead 16 after the fix (reduck-sqlite `f4ef1cd`: scans only) | 1.2 s  | 4.3 s    | 5.1 GB     | 5.1 min      |
| + one query per token first, all at once (`--warm-tokens`)        | 0.72 s | 2.5 s    | 4.7 GB     | 3.1 min      |
| read-ahead 0, cache 2 GiB                                         | 0.19 s | 0.05 s   | 0.85 GB    | 0.9 min      |
| all the chunks first (`--prefetch`: 27 s, 32 GETs at a time)      | 1 ms   | 2 ms     | 0.85 GB    | 27 s + 0.2 s |

- **More than the database was downloaded** because the cache (256 MiB)
  is smaller than the database: the 190 queries read 990 of its 1,003
  chunks, so the cache drops chunks that a later query fetches again.
  With a 2 GiB cache, each chunk was fetched exactly once (854 MB).
- **A wait costs 51 ms:** 24.5 ms for the GET of a whole chunk (0.87 MB)
  and 24 ms of CPU (21.7 ms of zstd in JavaScript, 2.5 ms of checksum). A
  range GET of 4 to 64 KiB takes 9.3 to 9.7 ms, and Node's native zstd
  decompresses a chunk in 1.1 ms.
- **A longer query reads more pages, although it finds fewer files.**
  FTS5 looks up each token of the phrase in each of the 17 segments
  before it compares their positions: about 20 pages per token. The
  matches of the first tokens do not make the lookups of the next ones
  cheaper. The suffix array instead compares the whole query at each of
  its 28 steps, so its time does not depend on the length.

Pages read per query, counted locally with a counting VFS
(`uv run --with apsw bench/fts5.py pages ...`), median:

| Queries              | 17 segments | after `optimize` (1 segment) |
| -------------------- | ----------- | ---------------------------- |
| 4 notes (1 token)    | 62          | 12                           |
| 16 notes (13 tokens) | 360         | 58                           |
| 32 notes (29 tokens) | 630         | 102                          |
| all 190              | 194         | 42                           |

Of the 194 pages, 55 are interior pages, which the reader fetches at
`open`. 46 are leaf pages of the term index `m_idx`, which is only
4.9 MiB.

## Visitors who make 1 or 2 searches

`bench/cold.mjs` opens a new `Database` for each visitor (an empty cache)
and runs 2 queries; 40 visitors. The variants were written by
`sqlite-convert` (reduck-sqlite, branch `convert`, `docs/convert.md`) from
the same shard, and read from S3 (eu-west-3, signed GETs, about 35 ms per
GET from the client). "Warm-up": before the phrase query, one query per
token of the phrase, all at once (`--warm-tokens`).

| Layout                                               | zstd   | Warm-up | Open + 1st query (p90) | 2nd query  | Downloaded |
| ---------------------------------------------------- | ------ | ------- | ---------------------- | ---------- | ---------- |
| as sqlite4all stores it: 17 segments, 1 MiB chunks   | fzstd  | no      | 4.64 s (8.5 s)         | 3.1 s      | 63 MB      |
| FTS5 `optimize`, 64 KiB frames, range GETs, hot list | fzstd  | no      | 0.87 s (1.9 s)         | 0.61 s     | 3.6 MB     |
| same                                                 | native | no      | 0.46 s (1.1 s)         | 0.32 s     | 3.6 MB     |
| `optimize`, 64 KiB chunks, hot list                  | native | yes     | 0.34 s (0.50 s)        | 0.11 s     | 3.5 MB     |
| `optimize`, 64 KiB frames, range GETs, no hot list   | fzstd  | yes     | **0.37 s** (0.47 s)    | **0.11 s** | 2.4 MB     |
| same                                                 | native | yes     | 0.30 s (0.41 s)        | 0.10 s     | 2.4 MB     |

The real `midi-shard0` through CloudFront, as sqlite4all stores it: 2.8 s
(p90 7.7 s). With a model of a CloudFront link that hits its cache (9 ms per
GET, 57 MB/s: `--dir` with `--latency` and `--mbps`), the best layout takes
0.19 s with fzstd and 0.12 s with the native zstd.

## Static suffix array: no SQLite, no server

`whatthemidi/static.py` (`what-the-midi static`) cuts the suffix array of
the shard into blocks. `web/src/lib/static-index.js` (7 KB, no
WebAssembly, no dependency) reads them with GETs and range GETs, in a
browser (`web/static.html`) or in Node. `bench/static.mjs` runs the same
190 queries and the same 40 visitors as `cold.mjs`, with the same network
model and the same S3 store.

### Layout

- **Leaf:** 4,096 consecutive suffixes. For each: its position (4 bytes),
  its key (front coded) and its `dup` (1 byte).
- **Key:** the shortest prefix that separates the suffix from its 2
  neighbours (7.4 notes on average). For a query, at most one key in the
  whole array is shorter than the query and equal to its start; the reader
  then GETs that suffix from the text (75 of the 190 queries, 1 range GET).
- **`dup`:** the LCP with the next suffix of the same file. In the range
  of a query of m notes, `files = matches - #{dup >= m}`: the number of
  files with no list of files. The reader GETs the `dup` bytes only when
  the matches fill more than the 2 leaves at their ends (6 of the 190
  queries, at most 245 KB).
- **Internal nodes:** 256 children. The root (10 KB) comes with the
  manifest at `open`.

A search is 2 rounds of GETs (1 internal node, then the leaves of the
first and the last match, in parallel), plus at most 1 for the text and 1
for `dup`.

|        | Static suffix array                                   | FTS5 (`v6-f16`)                        |
| ------ | ----------------------------------------------------- | -------------------------------------- |
| Stored | 4.37 GB (leaves 3.82 GB, text and `dup` 0.27 GB each) | 0.87 GB                                |
| Build  | 65 s, 22 GB of RAM                                    | 26 min for FTS5, then `sqlite-convert` |

### Results

Visitors (`--run visitors`, 40 visitors, a new index each, then 2
queries), with the model of `cold.mjs` (9 ms per GET, 57 MB/s):

| Layout                                                     | Open   | 1st query | Open + 1st (p90)  | 2nd query | Downloaded  |
| ---------------------------------------------------------- | ------ | --------- | ----------------- | --------- | ----------- |
| FTS5 `v6-f16`, fzstd, warm-up (best of `cold.mjs`)         | 112 ms | 70 ms     | 185 ms (231 ms)   | 51 ms     | 2.4 MB      |
| FTS5 `v6-f16`, native zstd, warm-up                        | 68 ms  | 57 ms     | 124 ms (162 ms)   | 37 ms     | 2.4 MB      |
| static, 4,096 per leaf, 256 per node                       | 12 ms  | 27 ms     | **39 ms** (49 ms) | 27 ms     | **0.15 MB** |
| static, 16,384 per leaf, the root over the leaves (654 KB) | 28 ms  | 20 ms     | 48 ms (61 ms)     | 20 ms     | 1.1 MB      |
| static, keys of at least 32 notes (8.6 GB)                 | 12 ms  | 26 ms     | 38 ms (43 ms)     | 26 ms     | 0.30 MB     |

The 190 queries (`--run queries`, each on a new index, open not counted):

| Layout                            | Cold median | p90   | Max   | GETs | Downloaded | Warm     |
| --------------------------------- | ----------- | ----- | ----- | ---- | ---------- | -------- |
| static, 4,096 per leaf            | 27 ms       | 38 ms | 48 ms | 2    | 69 KB      | 0.014 ms |
| static, root over the leaves      | 21 ms       | 33 ms | 45 ms | 1    | 234 KB     | 0.007 ms |
| static, keys of at least 32 notes | 26 ms       | 28 ms | 43 ms | 2    | 141 KB     | 0.006 ms |

- **3 to 5 times faster than the best FTS5 layout in this model, with 16
  times fewer bytes.** The time is the number of rounds of GETs: 2 for a
  search, as many as the tree has levels, and it does not depend on the
  number of notes of the query.
- **The keys of at least 32 notes** remove the text GET of the long
  queries (p90 28 ms instead of 38 ms), for twice the storage.
- **The root over the leaves** saves a round, but `open` downloads 654 KB
  and each leaf is 234 KB.
- **Warm** (the nodes in memory): 0.014 ms, about the time of the suffix
  array in Python (0.027 ms), 26 times faster than FTS5 warm (0.37 ms).
- **Correct:** for the 190 queries, the same numbers of files as the
  suffix array and FTS5. `tests/test_static.py` compares the reader with
  a brute-force search, for 3 tree shapes.

### On S3

The same bucket as `cold.mjs` (eu-west-3, signed GETs, the `s3Store` of
the reduck-sqlite reader), on 18 November at 23:00. The FTS5 rows were
measured again at the same time (yesterday at 20:50: 366 ms and 303 ms).

| Layout                                                   | Open   | 1st query | Open + 1st (p90)    | 2nd query | Downloaded  |
| -------------------------------------------------------- | ------ | --------- | ------------------- | --------- | ----------- |
| FTS5 `v6-f16`, fzstd (what a browser runs), warm-up      | 186 ms | 245 ms    | 424 ms (603 ms)     | 180 ms    | 2.4 MB      |
| FTS5 `v6-f16`, native zstd, warm-up                      | 137 ms | 170 ms    | 312 ms (410 ms)     | 103 ms    | 2.4 MB      |
| static, 4,096 per leaf, files of 64 MiB                  | 34 ms  | 127 ms    | 161 ms (222 ms)     | 128 ms    | **0.15 MB** |
| static, 4,096 per leaf, 1 file per level                 | 36 ms  | 117 ms    | 151 ms (198 ms)     | 120 ms    | **0.15 MB** |
| static, root over 16,384-suffix leaves, 1 file per level | 54 ms  | 88 ms     | **140 ms** (206 ms) | 93 ms     | 1.1 MB      |

The 190 queries, each on a new index (open not counted):

| Layout                       | Cold median | p90    | Max    |
| ---------------------------- | ----------- | ------ | ------ |
| static, files of 64 MiB      | 136 ms      | 192 ms | 252 ms |
| static, 1 file per level     | 120 ms      | 170 ms | 224 ms |
| static, root over the leaves | 94 ms       | 140 ms | 258 ms |

- **Open and the first search: 2.2 to 3 times faster than FTS5**
  (140 ms against 312 ms with native zstd, 424 ms with the fzstd of a
  browser), with 2 to 16 times fewer bytes. The second search takes about
  the same time (93 to 120 ms against 103 ms): after its first search,
  the FTS5 reader has the hot pages in its cache.
- **A round of GETs costs 30 to 90 ms on S3, not 9 ms.** A range GET takes
  about 32 ms in a region of an object that was read a short time before
  (the same bytes, or up to 64 KB from them), and about 72 ms (55 to 107
  ms) in a region that was not read. The size of the range has almost no
  effect (4 KB: 37 ms, 256 KB: 37 ms). A search reads one leaf of 3.8 GB
  of leaves: almost always a region that was not read. Measured with
  sequential range GETs of 60 KB on one connection.
- **1 file per level** (now the default) is 10 ms faster than files of 64
  MiB: with 61 files of leaves, most searches read a file that no search
  read a short time before.
- **The root over the leaves** removes the round of the internal node (a
  region that many searches read, so about 32 ms), for 654 KB at open.

For a public page: a public bucket or CloudFront, and a CORS rule that
allows the `Range` header. A static index of the full index (1.74 G
notes) is about 7 shards like this one, or one index in 2 parts of up to
2^32 notes: the time of a search stays the same, because the number of
rounds depends on the levels of the tree only.

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
uv run bench/fts5.py queries data/shard data/bench/fts5-shard.json
node bench/reader.mjs --base https://<cloudfront domain> --db <database id> [--read-ahead 0]
```

Static suffix array:

```sh
uv run what-the-midi static data/shard data/static/shard-l4k --names-from data/db-all/part-000   # 65 s
node bench/static.mjs --dir data/static/shard-l4k --run check
node bench/static.mjs --dir data/static/shard-l4k --run visitors --out data/bench/static/dir-l4k.json
node bench/static.mjs --dir data/static/shard-l4k --run queries --out data/bench/static/dir-l4k-queries.json
aws s3 sync data/static/shard-l4k s3://<bucket>/static/v1/shard-l4k
node bench/static.mjs --s3 <bucket> --region eu-west-3 --prefix static/v1/shard-l4k --run visitors
```

`bench/reader.mjs` needs the reader built in `../reduck-sqlite/reader`
(`npm run build`), and `data/bench/shard-queries.json`: the queries of
`fts5.py` with their number of files, which the script checks.
