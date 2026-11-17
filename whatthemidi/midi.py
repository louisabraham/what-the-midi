"""Melody extraction from MIDI files.

The melody of a file is the highest note at each onset, over all the
tracks except drums.
"""

from dataclasses import dataclass

import numpy as np
from symusic import Score

DEFAULT_MSPQ = 500_000  # microseconds per quarter note = 120 BPM


@dataclass
class Melody:
    pitches: np.ndarray  # uint8, MIDI note numbers
    ticks: np.ndarray  # uint32, onset of each note
    seconds: np.ndarray  # float32, onset of each note
    ppq: int  # ticks per quarter note

    def __len__(self):
        return len(self.pitches)


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


def melody_from_score(score: Score) -> Melody:
    ppq = score.ticks_per_quarter
    if ppq <= 0:
        raise ValueError(f"unsupported time division: {ppq}")
    times, pitches = [], []
    for track in score.tracks:
        if track.is_drum:
            continue
        notes = track.notes.numpy()
        times.append(notes["time"])
        pitches.append(notes["pitch"])
    if times:
        t = np.concatenate(times).astype(np.int64)
        p = np.concatenate(pitches).astype(np.int16)
    else:
        t = np.zeros(0, np.int64)
        p = np.zeros(0, np.int16)

    # sort by time, then highest pitch first, and keep the first note of each onset
    order = np.lexsort((-p, t))
    t, p = t[order], p[order]
    keep = np.ones(len(t), dtype=bool)
    keep[1:] = t[1:] != t[:-1]
    t, p = np.maximum(t[keep], 0), p[keep]

    tempos = score.tempos.numpy()
    seconds = ticks_to_seconds(t, tempos["time"], tempos["mspq"], ppq)
    return Melody(
        pitches=p.astype(np.uint8),
        ticks=t.astype(np.uint32),
        seconds=seconds.astype(np.float32),
        ppq=int(ppq),
    )


def parse_midi(path) -> Melody:
    return melody_from_score(Score(str(path)))
