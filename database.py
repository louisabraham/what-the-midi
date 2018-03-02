from glob import iglob
import os.path
from itertools import chain, groupby
from operator import itemgetter

from tqdm import tqdm
import smf

from divufsort import GeneralizedSuffixArray


class MidiDataBase(GeneralizedSuffixArray):
    def __init__(self):
        super().__init__()
        self.pos = {}
        self.length = {}

    def add_all_midi(self, path, progress_bar=True):
        spath = (os.path.join(path, '**.' + ext) for ext in ('mid', 'MID'))
        file = None if progress_bar else open(os.devnull, 'w')
        with tqdm(unit='B', unit_scale=True, unit_divisor=1024, file=file) as pbar:
            for p in chain(*map(iglob, spath)):
                key = p[len(path):]
                self.add_midi(key, p)
                pbar.update(os.path.getsize(p))

    def add_midi(self, key, path):
        pitches, pos, length = self.parse_midi(path)

        # super().add_document(key, pitches)
        self.documents[key] = len(pitches)
        self.text += pitches

        self.pos[key] = pos
        self.length[key] = length

    @staticmethod
    def parse_midi(path):
        midifile = smf.SMF(path)
        notes_on = []
        for event in midifile.events:
            # if it is as "Note On" event, append the time and the pitch
            if event.midi_buffer[0] == 144:
                notes_on.append((event.time_pulses, event.midi_buffer[1]))
        notes_on = [(t, max(map(itemgetter(1), notes)))
                    for t, notes in groupby(notes_on, itemgetter(0))]
        ticks = list(map(itemgetter(0), notes_on))
        pitches = bytes(map(itemgetter(1), notes_on))
        return pitches, ticks, event.time_pulses

    def search(self, pattern):
        for name, offset in super().search(pattern):
            yield name, self.pos[name][offset], self.length[name]


import pickle
import ctypes
from array import array


def generate(path):
    db = MidiDataBase()
    db.add_all_midi(path)
    db.generate()
    return db


def save(db, path='db.pkl'):
    sa = db.sa
    db.sa = array('i', sa)
    with open(path, 'wb') as f:
        pickle.dump(db, f)
    db.sa = sa


def load(path='db.pkl'):
    with open(path, 'rb') as f:
        db = pickle.load(f)
    db.sa = (ctypes.c_int * len(db.sa))(*db.sa)
    return db
