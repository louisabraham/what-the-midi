# collect: download, deduplicate, and index MIDI collections

```sh
uv run collect/run.py list                      # the sources
uv run collect/run.py all midiman magic_of_midi # download + ingest
uv run collect/run.py stats                     # duplicates per source
uv run collect/run.py metadata                  # titles from the GigaMIDI CSV (by MD5)
uv run collect/run.py neardup                   # group the near duplicates
uv run collect/run.py build data/db-all         # build the index
uv run what-the-midi serve data/db-all
```

Without source names, `all`, `download` and `ingest` process all the
sources. `--delete-archives` deletes each archive after its ingestion.
GigaMIDI is gated: accept its terms on the dataset page, then download
`Final_GigaMIDI_V2.0_Final.zip` and
`Final-Metadata-Extended-GigaMIDI-Dataset-updated.csv` (with a browser,
or `download` with `HF_TOKEN`) to `data/archives/gigamidi/`. The CSV
gives "Artist - Title" for 208k files, found by MD5 in all the
collections.

## Files

| File          | Purpose                                                                          |
| ------------- | -------------------------------------------------------------------------------- |
| `sources.py`  | List of the collections, in priority order                                       |
| `download.py` | Resumable downloads (curl) to `data/archives/<source>/`, with a disk space check |
| `ingest.py`   | Reads the MIDI files from the archives, removes duplicates, stores the melodies  |
| `metadata.py` | Titles and artists from the GigaMIDI CSV                                         |
| `neardup.py`  | Groups the almost identical melodies                                             |
| `build.py`    | Builds the index from the stored melodies                                        |
| `evaluate.py` | Searches 10 known tunes and prints the rank of the right piece                   |
| `run.py`      | Command line                                                                     |

## Duplicates

The collections overlap: Magic of MIDI contains MIDIMAN, GigaMIDI contains
Lakh, Monster contains Los Angeles, and so on. `ingest.py` removes 2 kinds
of duplicates:

1. **Same bytes** (MD5 of the file). The file is not parsed. Its path goes
   to the `aliases` table.
2. **Same melody** (hash of the pitch sequence that the index stores).
   Many files are the same song with different instruments, tempo, or
   metadata. Some collections (Monster) rewrote all their files, so their
   MD5s are all new: only this check finds their duplicates.

Melodies with fewer than 8 notes are not indexed.

3. **Almost the same melody** (`neardup.py`): another sequencing of the
   same piece, a fixed wrong note, a different ending. Two melodies are
   near duplicates when they share at least half of their 8-note
   sequences (Jaccard similarity, estimated with MinHash and found with
   locality-sensitive hashing) and their lengths differ by less than 25 %
   (a movement is not a copy of the whole work). Groups form around
   leaders, the melodies with the most files: each member is similar to
   its leader. 41 s for 4.6 M melodies. The index keeps one melody per
   group: the one with the most files, then the longest. The result card
   shows the number of versions and the files of the whole group.

Each melody gets the best name among all its files: the first source in
`sources.py` (sources with artist and title names come first), then the
shortest path. Several collections name their files by checksum
(`MIDIs/2/263c269c….mid`). For those, the name is the title written in
the MIDI file, when there is one: the name of the first track (the
sequence name), a text event of the first track, or a `Title: …` text.
Generic names ("Piano", "Track 1") and software credits are skipped. The
result cards show this title too.

The index stores the number of files with each melody (its copies). When 2 files have the same number of matches, the file with
more copies comes first: a melody found in many files is probably a
well-known song.

## Results (November 2026)

| Source        | Files          | Same bytes | Same melody | < 8 notes | Errors | New melodies  | Notes      |
| ------------- | -------------- | ---------- | ----------- | --------- | ------ | ------------- | ---------- |
| lakh_clean    | 17,243         | 0          | 2,985       | 6         | 77     | 14,175        | 24 M       |
| midiman       | 127,693        | 27,181     | 15,232      | 677       | 405    | 84,198        | 91 M       |
| magic_of_midi | 168,561        | 114,643    | 9,641       | 586       | 235    | 43,456        | 43 M       |
| lakh_full     | 177,640        | 141,318    | 15,083      | 28        | 203    | 21,008        | 39 M       |
| bread         | 943,109        | 563,000    | 113,085     | 6,630     | 480    | 259,914       | 290 M      |
| los_angeles   | 404,715        | 281,939    | 4,582       | 88        | 145    | 117,961       | 157 M      |
| monster       | 2,335,783      | 90,444     | 965,583     | 364,591   | 59     | 915,106       | 373 M      |
| sourdough     | 4,979,331      | 598,662    | 843,087     | 697,635   | 1,901  | 2,838,046     | 886 M      |
| gigamidi      | 2,136,218      | 716,855    | 782,837     | 295,071   | 0      | 341,455       | 145 M      |
| **total**     | **11,390,293** |            |             |           |        | **4,635,319** | **2.05 G** |

Each row counts the duplicates of the rows ingested before it (bread was
ingested before los_angeles, gigamidi last). The near duplicates then
make 4,190,186 groups: 445,133 melodies fewer.

## Data layout

```
data/archives/<source>/...         downloaded archives
data/store/catalog.sqlite          files, aliases, melodies, sources tables
data/store/melodies/<source>-NNN.npz   melodies to index (pitches, ticks, tempo changes)
data/db-all/                       the index
```

No MIDI file is extracted to the disk, except for 7z archives (Python has
no fast 7z reader: `bsdtar` extracts them to a temporary directory).

Useful queries:

```sh
sqlite3 data/store/catalog.sqlite "SELECT status, COUNT(*) FROM files GROUP BY status"
sqlite3 data/store/catalog.sqlite "SELECT error, COUNT(*) FROM files WHERE status = 'error' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```
