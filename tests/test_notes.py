import pytest

from whatthemidi.notes import NoteError, midi_to_name, parse_query


@pytest.mark.parametrize(
    "query, expected",
    [
        ("C4", [60]),
        ("A4", [69]),
        ("C-1 G9", [0, 127]),
        ("c#5, Db5 bb3 B3 F♯4 G♭4", [73, 73, 58, 59, 66, 66]),
        ("60 62,64", [60, 62, 64]),
        # no octave: first note in octave 4, then the closest to the previous note
        ("C D E", [60, 62, 64]),
        ("G4 C", [67, 72]),
        ("G4 D", [67, 62]),
        ("B3 C", [59, 60]),
        ("C4 F#", [60, 66]),  # a tritone goes up
        ("  E4   ", [64]),
        ("", []),
    ],
)
def test_parse_query(query, expected):
    assert parse_query(query) == expected


@pytest.mark.parametrize("query, token", [("C4 H4", "H4"), ("C4 128", "128"), ("G9 A9", "A9"), ("Cx", "Cx")])
def test_parse_query_errors(query, token):
    with pytest.raises(NoteError) as e:
        parse_query(query)
    assert e.value.token == token


def test_midi_to_name():
    assert [midi_to_name(n) for n in (0, 60, 61, 69, 127)] == ["C-1", "C4", "C#4", "A4", "G9"]
    for n in range(128):
        assert parse_query(midi_to_name(n)) == [n]
