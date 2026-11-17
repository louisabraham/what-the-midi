# what-the-midi

Want to identify a melody? Enter the notes, or play them on the
on-screen piano. The app searches them in a MIDI collection in less than
a millisecond, shows the score around each match, and plays it.

The command line tool `midi-grep.py` searches a pattern of MIDI pitches
in MIDI files, without an index.

## Usage

Requirements: [uv](https://docs.astral.sh/uv/) and Node.js.

```sh
uv sync
(cd web && npm install && npm run build)

# index a directory of MIDI files
uv run what-the-midi index path/to/midi/files data/db

# or download the public MIDI collections, remove duplicates, and index them
uv run collect/run.py all
uv run collect/run.py build data/db

uv run what-the-midi serve data/db          # http://127.0.0.1:8765
uv run what-the-midi search data/db "E5 D#5 E5 D#5 E5 B4 D5 C5 A4"
uv run what-the-midi search data/db "E E F G G F E D" --transpose
```

[`collect/`](collect/README.md) downloads 9 public collections (the 130k
files of https://redd.it/3ajwe4, Lakh MIDI, Los Angeles MIDI, GigaMIDI…),
removes the files with the same bytes or the same melody, and builds
the index.

Frontend development: run `serve` on port 8765, then `npm run dev` in
`web/`. Vite sends `/api` requests to the server.

Tests: `uv run pytest`.

## How it works

### Melody extraction

[symusic](https://github.com/Yikai-Liao/symusic) (a C++ MIDI parser)
reads the files. When symusic refuses a file (for example a data byte
above 127), [mido](https://github.com/mido/mido) reads it and clips the
bad bytes. We keep only the melody: the highest note at each note onset,
over all the tracks except drums. Python processes parse the files in
parallel.

### Index

All the melodies go in one array of notes, with a separator after each
file. We build 4 texts over this array, one for each search mode:

| Mode                      | Value at note `i`              |
| ------------------------- | ------------------------------ |
| exact                     | `pitch[i]`                     |
| ignore octave             | `pitch[i] % 12`                |
| any key (transposition)   | `pitch[i+1] - pitch[i] + 127`  |
| any key and ignore octave | `(pitch[i+1] - pitch[i]) % 12` |

A transposed melody has the same intervals, so the transposition modes
need one search, not 12. The separator (255) is in no query, so a match
never continues from one file into the next.

[pydivsufsort](https://github.com/louisabraham/pydivsufsort) builds a
suffix array for each text (to learn about suffix arrays:
https://louisabraham.github.io/notebooks/suffix_arrays.html).

The index also stores the tick of each note, and the tempo changes of
each file to convert ticks to seconds. That is 24 bytes per note. The
suffix arrays use 32-bit integers, so one index holds at most 2^31
notes: a larger collection is split in parts (`part-000/`, `part-001/`…)
that the server searches one after the other.

### Search

`sa_search` finds the range of suffixes that start with the query. We
group the first 100,000 matches by file, and return the files with the
best score first: `log2(1 + matches in the file) + log2(copies of its
melody)`. A melody found in many files is probably a well-known song. A
file that repeats the query many times is often a loop or an exercise,
so the number of matches counts less and less. The server
returns at most 50 files and accepts at most 64 notes per query.

### Memory

The index is a directory of `.npy` files. The server memory-maps them:
the operating system reads only the pages that a search uses, and can
remove them from RAM when it needs memory.

### Note names

English convention: middle C is C4 (MIDI 60). The query accepts note
names (`C4`, `D#5`, `Bb3`, `F♯4`) and MIDI numbers (`60`). A note
without an octave number goes to the octave closest to the previous note.

## Performance

On an Apple M4 Max (16 cores, 128 GB RAM):

| Index | Files | Unique melodies | Notes | Index on disk | Build |
| --- | --- | --- | --- | --- | --- |
| Lakh `clean_midi` | 17k | 14k | 24 M | 0.6 GB | 4 s |
| 8 public collections (`collect/`) | 9.3 M | 4.3 M | 1.9 G | 43 GB, 2 parts | 7.6 min |

- Reading and deduplicating the 9.3 M files: 52 min, most of it for
  the 5 M files of Sourdough.
- Search in the full index: 1 to 2 ms, about 30 ms when the disk cache
  is cold.
- The server keeps the file names and titles in RAM (1.7 GB for the full
  index) and memory-maps the rest.
- The build needs about 45 GB of RAM for the full index.

## API

- `GET /api/search?q=E5+D%235+E5&octave=0&transpose=0&limit=10&context=8`
- `GET /api/context?doc=12&pos=40&len=9&context=8`: notes around a match
- `GET /api/stats`

## Limitations

- A match must be a sequence of consecutive melody notes. When the
  melody holds a note and the accompaniment plays a new one, the
  accompaniment note goes into the melody, and a long query does not
  match. Use shorter queries.
- The ranking uses only the number of matches in each file and the
  number of copies of its melody. Different arrangements of a song are
  different melodies, so their copies are not added together.

## TODO

- Index each track separately, to find melodies that have accompaniment
  notes between their notes
- Approximate search (a wrong note or a missing note in the query)
