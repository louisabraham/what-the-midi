# collect: download, deduplicate, and index MIDI collections

```sh
uv run collect/run.py list                      # the sources
uv run collect/run.py all midiman magic_of_midi # download + ingest
uv run collect/run.py stats                     # duplicates per source
uv run collect/run.py build data/db-all         # build the index
uv run what-the-midi serve data/db-all
```

Without source names, `all`, `download` and `ingest` process all the
sources. `--delete-archives` deletes each archive after its ingestion.
Gated sources (GigaMIDI) need `HF_TOKEN` and access granted on the
dataset page.

## Files

| File          | Purpose                                                                          |
| ------------- | -------------------------------------------------------------------------------- |
| `sources.py`  | List of the collections, in priority order                                       |
| `download.py` | Resumable downloads (curl) to `data/archives/<source>/`, with a disk space check |
| `ingest.py`   | Reads the MIDI files from the archives, removes duplicates, stores the melodies  |
| `build.py`    | Builds the index from the stored melodies                                        |
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

Each melody gets the best name among all its files: the first source in
`sources.py` (sources with artist and title names come first), then the
shortest path. The index stores the number of files with each melody
(its copies). When 2 files have the same number of matches, the file with
more copies comes first: a melody found in many files is probably a
well-known song.

## Data layout

```
data/archives/<source>/...         downloaded archives
data/store/catalog.sqlite          files, aliases, melodies, sources tables
data/store/melodies/<source>-NNN.npz   melodies to index (pitches, ticks, seconds)
data/db-all/                       the index
```

No MIDI file is extracted to the disk, except for 7z archives (Python has
no fast 7z reader: `bsdtar` extracts them to a temporary directory).

Useful queries:

```sh
sqlite3 data/store/catalog.sqlite "SELECT status, COUNT(*) FROM files GROUP BY status"
sqlite3 data/store/catalog.sqlite "SELECT error, COUNT(*) FROM files WHERE status = 'error' GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
```
