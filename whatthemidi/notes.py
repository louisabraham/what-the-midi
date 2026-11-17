"""Conversion between note names and MIDI note numbers.

English (scientific pitch) convention: middle C is C4 = 60, A4 = 69.
"""

import re

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
STEPS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ACCIDENTALS = {"#": 1, "♯": 1, "b": -1, "♭": -1}
DEFAULT_OCTAVE = 4

_NOTE_RE = re.compile(r"^([A-Ga-g])([#♯b♭]*)(-?\d+)?$")
_SPLIT_RE = re.compile(r"[\s,;]+")


class NoteError(ValueError):
    """A token of the query is not a note."""

    def __init__(self, token, reason):
        super().__init__(f"{token!r}: {reason}")
        self.token = token
        self.reason = reason


def midi_to_name(midi: int) -> str:
    """60 -> 'C4'"""
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def _closest(pitch_class: int, previous: int) -> int:
    """MIDI number with this pitch class that is closest to previous.
    A tritone goes up."""
    up = (pitch_class - previous) % 12
    return previous + up if up <= 6 else previous + up - 12


def parse_token(token: str, previous: int | None = None) -> int:
    """Converts one token to a MIDI number.

    A token is a MIDI number ('60') or a note name ('C4', 'c#5', 'Bb3',
    'F♯'). Without an octave, the note is the closest to previous
    (or in octave 4 for the first note).
    """
    if token.isdigit():
        midi = int(token)
    else:
        m = _NOTE_RE.match(token)
        if not m:
            raise NoteError(token, "not a note name (e.g. C4, F#3, Bb) or a MIDI number")
        letter, accidentals, octave = m.groups()
        pitch_class = STEPS[letter.upper()] + sum(ACCIDENTALS[a] for a in accidentals)
        if octave is not None:
            midi = (int(octave) + 1) * 12 + pitch_class
        elif previous is None:
            midi = (DEFAULT_OCTAVE + 1) * 12 + pitch_class
        else:
            midi = _closest(pitch_class % 12, previous)
    if not 0 <= midi <= 127:
        raise NoteError(token, "outside the MIDI range C-1 (0) to G9 (127)")
    return midi


def parse_query(query: str) -> list[int]:
    """'G4 D5 b a' -> [67, 74, 71, 69]"""
    notes = []
    for token in _SPLIT_RE.split(query.strip()):
        if token:
            notes.append(parse_token(token, notes[-1] if notes else None))
    return notes
