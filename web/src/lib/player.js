// Small WebAudio synthesizer to play notes
let ctx = null;
let playing = [];

function audio() {
  ctx ??= new AudioContext();
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

const frequency = (midi) => 440 * 2 ** ((midi - 69) / 12);

function tone(midi, start, duration) {
  const ac = audio();
  const osc = ac.createOscillator();
  const gain = ac.createGain();
  osc.type = 'triangle';
  osc.frequency.value = frequency(midi);
  const end = start + Math.max(duration, 0.08);
  gain.gain.setValueAtTime(0, start);
  gain.gain.linearRampToValueAtTime(0.3, start + 0.01);
  gain.gain.exponentialRampToValueAtTime(0.12, start + 0.15);
  gain.gain.setValueAtTime(0.12, end - 0.05);
  gain.gain.linearRampToValueAtTime(0, end);
  osc.connect(gain).connect(ac.destination);
  osc.start(start);
  osc.stop(end + 0.01);
  playing.push(osc);
}

export function stop() {
  for (const osc of playing) {
    try { osc.stop(); } catch { /* already stopped */ }
  }
  playing = [];
}

export function playNote(midi, duration = 0.4) {
  tone(midi, audio().currentTime, duration);
}

// notes: [{ p, sec }]; returns the duration in seconds
export function playNotes(notes) {
  stop();
  if (!notes.length) return 0;
  const t0 = audio().currentTime + 0.05;
  const first = notes[0].sec;
  notes.forEach((n, i) => {
    const next = notes[i + 1];
    const length = next ? Math.min(next.sec - n.sec, 1.5) * 0.95 : 0.6;
    tone(n.p, t0 + n.sec - first, length);
  });
  const last = notes[notes.length - 1];
  return last.sec - first + 0.6;
}
