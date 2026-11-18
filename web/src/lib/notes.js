// English (scientific pitch) convention: middle C = C4 = MIDI 60
export const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

export const midiToName = (midi) => `${NAMES[midi % 12]}${Math.floor(midi / 12) - 1}`;

export const isBlack = (midi) => NAMES[midi % 12].length > 1;

// VexFlow key, e.g. 61 -> "c#/4"
export const vexKey = (midi) =>
  `${NAMES[midi % 12].toLowerCase()}/${Math.floor(midi / 12) - 1}`;

// note values in quarter notes -> VexFlow durations
const DURATIONS = [
  [0.25, '16'], [0.375, '16d'], [0.5, '8'], [0.75, '8d'], [1, 'q'],
  [1.5, 'qd'], [2, 'h'], [3, 'hd'], [4, 'w'],
];

// Closest VexFlow duration (in log scale) to a length in quarter notes
export function quantize(quarters) {
  if (!(quarters > 0)) return 'q';
  let best = DURATIONS[0];
  for (const d of DURATIONS) {
    if (Math.abs(Math.log(d[0] / quarters)) < Math.abs(Math.log(best[0] / quarters))) best = d;
  }
  return best[1];
}

export function formatTime(seconds) {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const STEPS = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const ACCIDENTALS = { '#': 1, '♯': 1, b: -1, '♭': -1 };

/**
 * 'G4 D5 b a' -> [67, 74, 71, 69], with the rules of whatthemidi/notes.py:
 * MIDI numbers or note names; a name without an octave is the note closest
 * to the previous one (a tritone goes up), or in octave 4 for the first.
 */
export function parseQuery(query) {
  const notes = [];
  for (const token of query.trim().split(/[\s,;]+/)) {
    if (!token) continue;
    let midi;
    if (/^\d+$/.test(token)) {
      midi = Number(token);
    } else {
      const m = /^([A-Ga-g])([#♯b♭]*)(-?\d+)?$/u.exec(token);
      if (!m) throw new Error(`${token}: not a note name (e.g. C4, F#3, Bb) or a MIDI number`);
      const pc = STEPS[m[1].toUpperCase()] + [...m[2]].reduce((s, a) => s + ACCIDENTALS[a], 0);
      const prev = notes.at(-1);
      if (m[3] !== undefined) midi = (Number(m[3]) + 1) * 12 + pc;
      else if (prev === undefined) midi = 60 + pc;
      else {
        const up = (((pc - prev) % 12) + 12) % 12;
        midi = up <= 6 ? prev + up : prev + up - 12;
      }
    }
    if (!(midi >= 0 && midi <= 127)) throw new Error(`${token}: outside the MIDI range C-1 (0) to G9 (127)`);
    notes.push(midi);
  }
  return notes;
}

// "Artist/Song name.mid" -> { title: "Song name", folder: "Artist" }
export function splitFile(file) {
  const parts = file.split('/');
  const title = parts.pop().replace(/\.(mid|midi|kar|rmi)$/i, '');
  return { title, folder: parts.join(' / ') };
}
