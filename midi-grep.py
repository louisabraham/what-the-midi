#!/usr/bin/env python3

"""
The MIT License (MIT)
Copyright (c) 2017 Louis Abraham <louis.abraham@yahoo.fr>

\x1B[34m\033[F\033[F

midi-grep allows to search a pattern of MIDI pitches
in MIDI files.
It uses libsmf to read the files fast and the
Knuth–Morris–Pratt algorithm when --melody=1.

\x1B[0m\x1B[36m\033[F\033[F

The pattern must appear as contiguous notes in the MIDI files,
accross all the tracks.
For instance, even if the melody is in a separate MIDI track,
midi-grep will not recognize it if there is a note in the
accompaniment strictly between two notes of the melody.

\x1B[0m\x1B[35m\033[F\033[F

TODO: (contributions by email are welcome)
    - add --transpose option to search for the 12 transposed patterns
    - add --ignore-octave option
    - more specific input format (chords / wildcards)
    - For the moment, the program displays the tick of the matches.
      It would be nice to add an option to display the score around
      the match in a friendly way / to play it.
      Using https://github.com/0xfe/vexflow to display
      the scores in the browser would be an idea.
    - allow to index a large database of files with
      Suffix Arrays
      https://louisabraham.github.io/notebooks/suffix_arrays.html
      https://github.com/debatem1/pydivsufsort
    - index https://redd.it/3ajwe4 (about 50M of notes,
      should be indexed in minutes)
    - deploy as a webapp

\x1B[0m\033[1m\033[F\033[F

example of usage:

    midi-grep 79,74,71,67,71,67,72,67,74,67,76,67 *.mid

\033[0m\033[F\033[F
"""

import sys
from itertools import islice, groupby
from operator import itemgetter
import argparse

try:
    import smf
except ImportError:
    print(
        "\x1B[1;31mpython-smf is required\n"
        "install it from http://das.nasophon.de/pysmf/\x1B[0m",
        file=sys.stderr,
    )
    sys.exit()


class CustomFormatter(
    argparse.ArgumentDefaultsHelpFormatter, argparse.RawDescriptionHelpFormatter
):
    pass


parser = argparse.ArgumentParser(
    prog="midi-grep", description=__doc__, formatter_class=CustomFormatter
)
parser.add_argument("pattern", help="MIDI note numbers separated by comas")
parser.add_argument("files", nargs="*", help="MIDI files to be searched in")
parser.add_argument(
    "--melody",
    type=int,
    choices=range(2),
    default=1,
    help="match only the highest note instead " "of any note in the chord",
)
args = parser.parse_args()

(*query,) = map(int, args.pattern.split(","))

if args.melody:

    def condition(note, chord):
        return note == max(chord)


else:

    def condition(note, chord):
        return note in chord


def kmp(notes_on, query):
    len_query = len(query)
    r = [0] * (len_query + 1)
    j = r[0] = -1
    for i in range(len_query):
        while j >= 0 and query[i] != query[j]:
            j = r[j]
        j += 1
        r[i + 1] = j
    j = 0
    for pos in range(len(notes_on)):
        notes_on[pos][1]
        while j >= 0 and notes_on[pos][1] != query[j]:
            j = r[j]
        j += 1
        if j == len_query:
            yield pos - len_query + 1
            j = r[j]


for path in args.files:
    midifile = smf.SMF(path)
    notes_on = []

    for event in midifile.events:
        # if it is as "Note On" event, append the time and the pitch
        if event.midi_buffer[0] == 144:
            notes_on.append((event.time_pulses, event.midi_buffer[1]))
    maxtick = event.time_pulses
    if args.melody:
        # get only the highest note
        notes_on = [
            (t, max(map(itemgetter(1), notes)))
            for t, notes in groupby(notes_on, itemgetter(0))
        ]
        for pos in kmp(notes_on, query):
            print("%s at tick %s/%s" % (path, notes_on[pos][0], maxtick))
    else:
        # group all the notes by their time
        notes_on = [
            (t, set(map(itemgetter(1), notes)))
            for t, notes in groupby(notes_on, itemgetter(0))
        ]
        # print(notes_on[:10])

        for pos in range(len(notes_on) - len(query) + 1):
            if all(
                n in notes for n, (_, notes) in zip(query, islice(notes_on, pos, None))
            ):
                print("%s at tick %s/%s" % (path, notes_on[pos][0], maxtick))
