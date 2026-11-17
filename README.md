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

uv run what-the-midi index path/to/midi/files data/db
uv run what-the-midi serve data/db          # http://127.0.0.1:8080
uv run what-the-midi search data/db "E5 D#5 E5 D#5 E5 B4 D5 C5 A4"
uv run what-the-midi search data/db "E E F G G F E D" --transpose
```

MIDI collections:

- [Lakh MIDI `clean_midi`](https://colinraffel.com/projects/lmd/):
  17k files named `Artist/Title.mid` (234 MB download).
- The 130k files of https://redd.it/3ajwe4.

Frontend development: run `serve` on port 8080, then `npm run dev` in
`web/`. Vite sends `/api` requests to the server.

Tests: `uv run pytest`.

## How it works

### Melody extraction

[symusic](https://github.com/Yikai-Liao/symusic) (a C++ MIDI parser)
reads the files. We keep only the melody: the highest note at each note
onset, over all the tracks except drums. Python processes parse the files
in parallel.

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

### Search

`sa_search` finds the range of suffixes that start with the query. We
group the first 100,000 matches by file, and return the files with the
most matches first. The server returns at most 50 files and accepts at
most 64 notes per query.

### Memory

The index is a directory of `.npy` files. The server memory-maps them:
the operating system reads only the pages that a search uses, and can
remove them from RAM when it needs memory. The server uses about 60 MB
of RAM with the 765 MB `clean_midi` index.

### Note names

English convention: middle C is C4 (MIDI 60). The query accepts note
names (`C4`, `D#5`, `Bb3`, `F♯4`) and MIDI numbers (`60`). A note
without an octave number goes to the octave closest to the previous note.

## Performance

`clean_midi` on an Apple M4 Max (16 cores):

- 17,029 files (224 files could not be read), 28.6 million notes
- Parse 1.1 s, suffix arrays 4.4 s
- Index on disk: 765 MB
- Search: less than 1 ms (8 ms for a 2-note query with 940,000 matches)

## API

- `GET /api/search?q=E5+D%235+E5&octave=0&transpose=0&limit=10&context=8`
- `GET /api/context?doc=12&pos=40&len=9&context=8`: notes around a match
- `GET /api/stats`

## Limitations

- A match must be a sequence of consecutive melody notes. When the
  melody holds a note and the accompaniment plays a new one, the
  accompaniment note goes into the melody, and a long query does not
  match. Use shorter queries.
- There is no ranking other than the number of matches in each file.

## TODO

- Index each track separately, to find melodies that have accompaniment
  notes between their notes
- Approximate search (a wrong note or a missing note in the query)
