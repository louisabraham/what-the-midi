# what-the-midi

Want to identify a melody? Just enter the notes and they will be
searched in our database within seconds. We use the data from
https://redd.it/3ajwe4.

The CLI tool midi-grep allows to search a pattern of MIDI pitches
in MIDI files.

# How it works

## Parse the midi files

We use https://github.com/stump/libsmf and its Cython binding
https://github.com/dsacre/pysmf to parse midi files. We are only
interested in the melody, so take only the highest note at each "Note
On" event, but we have to iterate through _all the tracks_. Luckily,
libsmf provides this feature.

## Index them

https://github.com/y-256/libdivsufsort implements fast suffix arrays (to
learn about them:
https://louisabraham.github.io/notebooks/suffix\_arrays.html). We use
the Python binding https://github.com/debatem1/pydivsufsort.

The C code is so fast that the suffix array generation is almost
instantaneous, while reading and indexing the MIDI files takes the most
time because of the disk speed and the slow Python code (even if PySMF
is really fast). The speed on my laptop is 1 MB/s.

## Search

Thanks to libdivufsort, we can retrieve the index of any pattern of
notes given as the input in the concatenation of all files.

The GeneralizedSuffixArray of pydivsufsort builds an array of the file
offsets and does a binary search to retrieve the file.

## Save

We use a little trick to save the suffix arrays: they must be ctypes
arrays because of the C binding, but those are not picklable. Therefore,
we convert them to arrays from the array module.

Thus, one can index the midi files only once, and reload quickly the
database with the pickle module.

# TODO

- Build a web app with Flask
- Compress database for limited RAM

## Additional features

- Restrict to first n matches to prevent DDoS (n = 10 for example)
- User friendly note to midi code conversion using the [english
  convention](http://www.electronics.dit.ie/staff/tscarff/Music_technology/midi/midi_note_numbers_for_octaves.htm)
- Ignore octave (needs another indexing of the database modulo 12)
- Search through transpositions (do 12 pattern queries with the
  "ignore octave" database)
- Use http://www.vexflow.com/ to display the original score around the
  match
- Fast indexing by writing the parse_midi function in C.
