import { Accidental, Dot, Formatter, Renderer, Stave, StaveNote } from 'vexflow/bravura';
import { isBlack, quantize, vexKey } from './notes.js';

const fontsReady = document.fonts?.load('30px Bravura').catch(() => {}) ?? Promise.resolve();

export const MATCH_COLOR = '#c2410c';

/**
 * Draws notes on one stave in `el`.
 * notes: [{ p, tick }]; with ppq > 0, durations come from the onset
 * differences, else all the notes are quarter notes.
 * Notes with index in [from, to) are in MATCH_COLOR.
 */
export async function renderScore(el, notes, { ppq = 0, from = 0, to = 0 } = {}) {
  const token = (el.dataset.token = String(Math.random()));
  await fontsReady;
  if (el.dataset.token !== token) return; // a newer render started
  el.replaceChildren();
  if (!notes.length) return;

  const mean = notes.reduce((s, n) => s + n.p, 0) / notes.length;
  const clef = mean >= 57 ? 'treble' : 'bass';
  const staveNotes = notes.map((n, i) => {
    const next = notes[i + 1];
    const duration = ppq && next ? quantize((next.tick - n.tick) / ppq) : 'q';
    const note = new StaveNote({ keys: [vexKey(n.p)], duration, clef, autoStem: true });
    if (isBlack(n.p)) note.addModifier(new Accidental('#'), 0);
    if (duration.endsWith('d')) Dot.buildAndAttach([note], { all: true });
    if (i >= from && i < to) note.setStyle({ fillStyle: MATCH_COLOR, strokeStyle: MATCH_COLOR });
    return note;
  });

  const width = 80 + staveNotes.length * 38;
  const renderer = new Renderer(el, Renderer.Backends.SVG);
  renderer.resize(width, 400);
  const ctx = renderer.getContext();
  const stave = new Stave(0, 150, width - 2);
  stave.addClef(clef).setContext(ctx).draw();
  const box = Formatter.FormatAndDraw(ctx, stave, staveNotes, { autoBeam: true });

  // fit the drawing: notes with ledger lines can go above or below the stave
  const top = Math.min(stave.getYForLine(0) - 25, box ? box.getY() - 10 : Infinity);
  const bottom = Math.max(stave.getYForLine(4) + 25, box ? box.getY() + box.getH() + 10 : 0);
  const svg = el.querySelector('svg');
  svg.setAttribute('viewBox', `0 ${top} ${width} ${bottom - top}`);
  svg.setAttribute('height', bottom - top);
  svg.style.width = `${width}px`;
  svg.style.height = `${bottom - top}px`;
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `Score with ${notes.length} notes`);
  // on a narrow screen, show the match first
  if (to > from) el.scrollLeft = Math.max(0, staveNotes[from].getAbsoluteX() - 70);
}
