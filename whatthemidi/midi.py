"""Melody extraction from MIDI files.

The melody of a file is the highest note at each onset, over all the
tracks except drums.
"""

import io
import os
import re
from dataclasses import dataclass

import numpy as np
from symusic import Score

DEFAULT_MSPQ = 500_000  # microseconds per quarter note = 120 BPM


@dataclass
class Melody:
    pitches: np.ndarray  # uint8, MIDI note numbers
    ticks: np.ndarray  # uint32, onset of each note
    ppq: int  # ticks per quarter note
    tempo_ticks: np.ndarray  # uint32, time of each tempo change, the first at 0
    tempo_mspq: np.ndarray  # uint32, microseconds per quarter note from that time

    def __len__(self):
        return len(self.pitches)

    @property
    def seconds(self):
        return ticks_to_seconds(self.ticks, self.tempo_ticks, self.tempo_mspq, self.ppq)


def tempo_map(tempo_ticks, tempo_mspq):
    """Sorted tempo changes, the first at tick 0, one per tick (the last one)."""
    tt = np.asarray(tempo_ticks, dtype=np.int64)
    mspq = np.asarray(tempo_mspq, dtype=np.int64)
    order = np.argsort(tt, kind="stable")
    tt, mspq = np.maximum(tt[order], 0), mspq[order]
    if len(tt) == 0 or tt[0] > 0:
        tt, mspq = np.r_[0, tt], np.r_[DEFAULT_MSPQ, mspq]
    last = np.r_[tt[1:] != tt[:-1], True]
    mspq = np.where(mspq > 0, mspq, DEFAULT_MSPQ)
    return tt[last].astype(np.uint32), mspq[last].astype(np.uint32)


def ticks_to_seconds(ticks, tempo_ticks, tempo_mspq, ppq):
    """Converts ticks to seconds with a tempo map.

    tempo_ticks: time of each tempo change, tempo_mspq: microseconds per
    quarter note from that time.
    """
    ticks = np.asarray(ticks, dtype=np.float64)
    order = np.argsort(tempo_ticks, kind="stable")
    tt = np.asarray(tempo_ticks, dtype=np.float64)[order]
    mspq = np.asarray(tempo_mspq, dtype=np.float64)[order]
    if len(tt) == 0 or tt[0] > 0:
        tt = np.r_[0.0, tt]
        mspq = np.r_[DEFAULT_MSPQ, mspq]
    sec_per_tick = mspq / ppq / 1e6
    # time in seconds at each tempo change
    start_sec = np.r_[0.0, np.cumsum(np.diff(tt) * sec_per_tick[:-1])]
    # with several changes at the same tick, side="right" takes the last one
    k = np.searchsorted(tt, ticks, side="right") - 1
    return start_sec[k] + (ticks - tt[k]) * sec_per_tick[k]


# earlier notes checked for a higher note that still sounds. 0 (default): no
# skyline rule. The rule finds songs whose accompaniment plays between the
# melody notes, but it drops the inner voices that a listener hears in
# keyboard music (Bach, Scarlatti): the approximate search handles both.
SKYLINE_LOOKBACK = int(os.environ.get("WTM_SKYLINE_LOOKBACK", 0))


def skyline(t, p, end, ppq, lookback=SKYLINE_LOOKBACK):
    """Mask of the notes to keep, among the highest notes of each onset.

    A note is dropped when an earlier, higher note still sounds at its
    onset: for example a left hand note played while the right hand holds
    the melody. An overlap shorter than a 32nd note does not count (legato
    playing). Notes are checked against the notes kept by a first pass, so
    a dropped note does not drop other notes.
    """
    tolerance = max(ppq // 8, 1)
    keep = np.ones(len(t), dtype=bool)
    for _ in range(2):
        blocked = np.zeros(len(t), dtype=bool)
        for k in range(1, min(lookback, len(t) - 1) + 1):
            blocked[k:] |= keep[:-k] & (p[:-k] > p[k:]) & (end[:-k] - t[k:] > tolerance)
        keep = ~blocked
    return keep


def melody_from_notes(times, pitches, tempo_ticks, tempo_mspq, ppq, durations=None,
                      lookback=None) -> Melody:
    """The melody of a file: the highest note at each onset, without the
    notes that start under a higher note that still sounds (see skyline).
    times, pitches, durations: all the non-drum notes."""
    if ppq <= 0:
        raise ValueError(f"unsupported time division: {ppq}")
    t = np.asarray(times, dtype=np.int64)
    p = np.asarray(pitches, dtype=np.int16)
    d = np.zeros(len(t), np.int64) if durations is None else np.asarray(durations, dtype=np.int64)
    # sort by time, then highest pitch first, and keep the first note of each onset
    order = np.lexsort((-p, t))
    t, p, d = t[order], p[order], d[order]
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = t[1:] != t[:-1]
    t, p, d = np.maximum(t[keep], 0), p[keep], d[keep]
    keep = skyline(t, p, t + np.maximum(d, 0), ppq,
                   SKYLINE_LOOKBACK if lookback is None else lookback)
    t, p = t[keep], p[keep]
    tempo_ticks, tempo_mspq = tempo_map(tempo_ticks, tempo_mspq)
    return Melody(
        pitches=p.astype(np.uint8),
        ticks=t.astype(np.uint32),
        ppq=int(ppq),
        tempo_ticks=tempo_ticks,
        tempo_mspq=tempo_mspq,
    )


def melody_from_score(score: Score) -> Melody:
    times, pitches, durations = [np.zeros(0, np.int64)], [np.zeros(0, np.int16)], [np.zeros(0, np.int64)]
    for track in score.tracks:
        if not track.is_drum:
            notes = track.notes.numpy()
            times.append(notes["time"])
            pitches.append(notes["pitch"])
            durations.append(notes["duration"])
    tempos = score.tempos.numpy()
    return melody_from_notes(
        np.concatenate(times), np.concatenate(pitches),
        tempos["time"], tempos["mspq"], score.ticks_per_quarter,
        durations=np.concatenate(durations),
    )


def melody_from_mido(data: bytes) -> Melody:
    """Slower parser that accepts data bytes above 127 (it clips them)."""
    import mido

    midi = mido.MidiFile(file=io.BytesIO(data), clip=True)
    times, pitches, durations, tempo_ticks, tempo_mspq = [], [], [], [], []
    for track in midi.tracks:
        tick, sounding = 0, {}  # (channel, pitch) -> indexes of the notes that sound
        for msg in track:
            tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0 and msg.channel != 9:
                sounding.setdefault((msg.channel, msg.note), []).append(len(times))
                times.append(tick)
                pitches.append(msg.note)
                durations.append(0)
            elif msg.type in ("note_off", "note_on") and sounding.get((msg.channel, msg.note)):
                i = sounding[msg.channel, msg.note].pop(0)
                durations[i] = tick - times[i]
            elif msg.type == "set_tempo":
                tempo_ticks.append(tick)
                tempo_mspq.append(msg.tempo)
    return melody_from_notes(times, pitches, tempo_ticks, tempo_mspq, midi.ticks_per_beat,
                             durations=durations)


def parse_midi_bytes(data: bytes) -> Melody:
    """Parses with symusic (fast), then with mido when symusic fails."""
    try:
        return melody_from_score(Score.from_midi(data))
    except Exception as first:
        try:
            return melody_from_mido(data)
        except Exception:
            raise first from None


def parse_midi(path) -> Melody:
    with open(path, "rb") as f:
        return parse_midi_bytes(f.read())


# ---------------------------------------------------------------- titles

_GENERIC = re.compile(
    r"^(track|tr|trk|tk|untitled|sequence|seq|song|title|new song|piano|grand piano|bass|"
    r"drums?|percussion|guitar|melody|strings?|vocals?|voice|lead|staff|instrument|channel|"
    r"ch|part|midi|general midi|gm|xg|gs|tempo|meta|conductor|copyright|unnamed|noname|new track|"
    r"system|setup|reset|\W*)\s*[-#:.]?\s*\d*$",
    re.I,
)
_NOT_TITLE = re.compile(
    r"generated|created|sequenced|arranged|transcribed|programm|copyright|\(c\)|©|www\.|http|"
    r"@|\.com\b|\.mid\b|\.mdv\b|karaoke|noteworthy|cakewalk|cubase|logic|finale|sibelius|encore|"
    r"converted|device\s*=|^(verse|chorus|intro|outro|bridge|ending|solo)\s*\d*$",
    re.I,
)
_TITLE_LABEL = re.compile(r"^(title|titolo|titre|titel|titulo|song|songname|name)\s*[:=]\s*(.{3,})$", re.I)
_MAX_EVENTS = 64  # events read at the start of each track

# General MIDI instrument names, and track names that tools write in every file
_GM = """acoustic grand piano|bright acoustic piano|electric grand piano|honky-tonk piano|
electric piano 1|electric piano 2|harpsichord|clavinet|celesta|glockenspiel|music box|vibraphone|
marimba|xylophone|tubular bells|dulcimer|drawbar organ|percussive organ|rock organ|church organ|
reed organ|accordion|harmonica|tango accordion|acoustic guitar (nylon)|acoustic guitar (steel)|
electric guitar (jazz)|electric guitar (clean)|electric guitar (muted)|overdriven guitar|
distortion guitar|guitar harmonics|acoustic bass|electric bass (finger)|electric bass (pick)|
fretless bass|slap bass 1|slap bass 2|synth bass 1|synth bass 2|violin|viola|cello|contrabass|
tremolo strings|pizzicato strings|orchestral harp|timpani|string ensemble 1|string ensemble 2|
synth strings 1|synth strings 2|choir aahs|voice oohs|synth choir|orchestra hit|trumpet|trombone|
tuba|muted trumpet|french horn|brass section|synth brass 1|synth brass 2|soprano sax|alto sax|
tenor sax|baritone sax|oboe|english horn|bassoon|clarinet|piccolo|flute|recorder|pan flute|
blown bottle|shakuhachi|whistle|ocarina|lead 1 (square)|lead 2 (sawtooth)|lead 3 (calliope)|
lead 4 (chiff)|lead 5 (charang)|lead 6 (voice)|lead 7 (fifths)|lead 8 (bass + lead)|pad 1 (new age)|
pad 2 (warm)|pad 3 (polysynth)|pad 4 (choir)|pad 5 (bowed)|pad 6 (metallic)|pad 7 (halo)|
pad 8 (sweep)|fx 1 (rain)|fx 2 (soundtrack)|fx 3 (crystal)|fx 4 (atmosphere)|fx 5 (brightness)|
fx 6 (goblins)|fx 7 (echoes)|fx 8 (sci-fi)|sitar|banjo|shamisen|koto|kalimba|bagpipe|fiddle|shanai|
tinkle bell|agogo|steel drums|woodblock|taiko drum|melodic tom|synth drum|reverse cymbal|
guitar fret noise|breath noise|seashore|bird tweet|telephone ring|helicopter|applause|gunshot|
project los angeles|electric piano|elec. piano|grand piano|acoustic guitar|electric guitar|
synth bass|synth pluck|synth lead|drum kit|lofi drum kit|scifi|perc|square|sawtooth|sine|triangle"""
_INSTRUMENTS = {name.strip() for name in _GM.replace("\n", "").split("|")}


def _is_instrument(text: str) -> bool:
    if re.search(r"\((classic|legacy)\)$|\bgtr\b|\bdrum ?kit\b|\bconcert harp\b", text, re.I):
        return True  # Online Sequencer instrument names
    t = re.sub(r"\s*\([^)]*\)\s*", " ", text.lower()).strip()  # "Elec. Piano (Classic)"
    t = re.sub(r"^(8-bit|8 bit|chip|retro)\s+", "", t)  # "8-Bit Triangle"
    t = re.sub(r"\s*\d+$", "", t)
    return text.lower() in _INSTRUMENTS or t in _INSTRUMENTS


def is_title(text) -> bool:
    """False for texts that are not titles: instrument names, generic
    names, software credits."""
    return bool(text) and 3 <= len(text) <= 120 and not _GENERIC.match(text) \
        and not _NOT_TITLE.search(text) and not _is_instrument(text) \
        and sum(c.isalpha() for c in text) >= 3 and not text.startswith("*")


def _varlen(data, pos):
    value = 0
    for _ in range(4):
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if byte < 0x80:
            break
    return value, pos


def _decode(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return " ".join(text.replace("\x00", " ").split())


def midi_texts(data: bytes):
    """[(track number, meta type, text)] of the text events (1: text,
    2: copyright, 3: track name) at the start of each track."""
    out = []
    pos, track = 14, 0
    try:
        while pos + 8 <= len(data):
            kind, size = data[pos : pos + 4], int.from_bytes(data[pos + 4 : pos + 8], "big")
            start, end = pos + 8, min(pos + 8 + size, len(data))
            pos = start + size
            if kind != b"MTrk":
                continue
            i, running, events = start, None, 0
            while i < end and events < _MAX_EVENTS:
                events += 1
                _, i = _varlen(data, i)  # delta time
                status = data[i]
                if status == 0xFF:
                    meta = data[i + 1]
                    length, i = _varlen(data, i + 2)
                    if meta in (1, 2, 3) and length:
                        out.append((track, meta, _decode(data[i : i + length])))
                    i += length
                elif status in (0xF0, 0xF7):
                    length, i = _varlen(data, i + 1)
                    i += length
                else:
                    if status & 0x80:
                        running = status
                        i += 1
                    if running is None:
                        break
                    i += 1 if running & 0xF0 in (0xC0, 0xD0) else 2
            track += 1
    except IndexError:
        pass
    return out


def midi_title(data: bytes) -> str | None:
    """Title of a MIDI file from its text events, or None: the name of
    the first track (the sequence name), else a text event of the first
    track. Generic names ("Piano", "Track 1") are skipped."""
    texts = midi_texts(data)
    for _, meta, text in texts:  # "Title: ..." in any track
        m = _TITLE_LABEL.match(text)
        if meta in (1, 3) and m and is_title(m.group(2)):
            return m.group(2).strip()
    for wanted in (3, 1):
        for track, meta, text in texts:
            if track == 0 and meta == wanted and is_title(text):
                return text
    return None
