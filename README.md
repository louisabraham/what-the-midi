# what-the-midi

## Data

Use the data from https://redd.it/3ajwe4.

## Parse the midi files

Use https://github.com/stump/libsmf to parse midi files. We are only interested in the melody,
so take only the highest note at each "Note On" event, but we have to iterate through *all the tracks*.
Luckily, libsmf provides this feature.

The Python code below uses the pysmf binding, but should be easily converted to C.
```python
midifile = smf.SMF(path)
notes_on = []

for event in midifile.events:
    # if it is as "Note On" event, append the time and the pitch
    if event.midi_buffer[0] == 144:
        notes_on.append((event.time_pulses, event.midi_buffer[1]))
maxtick = event.time_pulses
if args.melody:
    # get only the highest note
    notes_on = [(t, max(map(itemgetter(1), notes))) for t, notes
                in groupby(notes_on, itemgetter(0))]
```

`midifile.events` is defined [there](https://github.com/dsacre/pysmf/blob/master/src/smf.pyx#L226).
It uses the `smf_get_next_event` function.

The elements at the same "tick" in `notes_on` are merged to keep only the highest pitch.
It is possible to do it on the fly (replace the last element or add a new one) without
appending all the "Note On" events.

Now you can forget about the times of the events, and only keep the filenames with
their content encoded as `uint8_t*` (for memory saving) since there are only 128 < 256 possible pitches.

## Index them

https://github.com/y-256/libdivsufsort implements suffix arrays (to learn about them: https://louisabraham.github.io/notebooks/suffix_arrays.html).

Look into the examples to construct the suffix array of the concatenation of *all files* https://github.com/y-256/libdivsufsort/tree/master/examples.

## Search

Thanks to libdivufsort, we can retrieve the index of any pattern of notes given as the input in the concatenation of all files.

Now, we need to find the file associated to the index. Just build an array of the offsets of each file in the concatenation,
and do a binary search in it.

## Save

Save the offset index and the suffix array in a database file.

## Web App

The goal is to build a website (at least an API) that can communicate with the C program to send queries to it.

## Additional features

- Restrict to first n matches to prevent DDoS (n = 10 for example)
- User friendly note to midi code conversion using the
[english convention](http://www.electronics.dit.ie/staff/tscarff/Music_technology/midi/midi_note_numbers_for_octaves.htm)
- Ignore octave (needs another indexing of the database modulo 12)
- Search through transpositions (do 12 patters queries with the "ignore octave" database)
- Use http://www.vexflow.com/ to display the original score around the match
