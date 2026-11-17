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

// "Artist/Song name.mid" -> { title: "Song name", folder: "Artist" }
export function splitFile(file) {
  const parts = file.split('/');
  const title = parts.pop().replace(/\.(mid|midi|kar|rmi)$/i, '');
  return { title, folder: parts.join(' / ') };
}
