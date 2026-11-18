// static.html: the search with no server. The page reads a static index
// (whatthemidi/static.py) from a bucket with range requests.
//
// The index URL: ?index=<url>, else VITE_STATIC_INDEX at build time, else
// `what-the-midi serve-static` on port 8766.

import './app.css';
import { midiToName, parseQuery, splitFile } from './lib/notes.js';
import { StaticIndex, httpStore } from './lib/static-index.js';

const CONTEXT = 8;
const LIMIT = 10;
const EXAMPLES = [
  ['Für Elise', 'E5 D#5 E5 D#5 E5 B4 D5 C5 A4'],
  ['Ode to Joy', 'E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4'],
  ['Happy Birthday', 'G4 G4 A4 G4 C5 B4 G4 G4 A4 G4 D5 C5'],
];

const $ = (id) => document.getElementById(id);
const fmt = (n) => n.toLocaleString('en');
const kb = (b) => (b < 1e6 ? `${(b / 1e3).toFixed(1)} KB` : `${(b / 1e6).toFixed(2)} MB`);
const base = new URLSearchParams(location.search).get('index') ?? import.meta.env.VITE_STATIC_INDEX ?? 'http://127.0.0.1:8766';

const el = (tag, props = {}, ...children) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
};

let t = performance.now();
const opening = StaticIndex.open(httpStore(base));
opening.then((ix) => {
  const m = ix.manifest;
  $('about').textContent = `${fmt(m.files)} files, ${fmt(m.notes - m.files)} notes, read from ${base} `
    + `(open: ${Math.round(performance.now() - t)} ms, ${kb(ix.stats.bytes)})`;
}, (e) => {
  $('about').textContent = `Cannot open the index at ${base}: ${e.message}`;
  $('about').className = 'error';
});

let current = 0;

async function run(query) {
  const id = ++current;
  const summary = $('summary');
  const results = $('results');
  let notes;
  try {
    notes = parseQuery(query);
    if (!notes.length) return;
  } catch (e) {
    summary.textContent = e.message;
    summary.className = 'error';
    return;
  }
  summary.className = 'stats';
  summary.textContent = 'Searching…';
  const ix = await opening;
  const before = { ...ix.stats };
  t = performance.now();
  const r = await ix.search(notes, { limit: LIMIT });
  const searched = performance.now() - t;
  // the file of each match and the notes around it: 1 more round of GETs
  const [files, texts] = await Promise.all([
    r.positions.length ? ix.files(r.positions) : [],
    Promise.all(r.positions.map((p) => ix.text(p - CONTEXT, notes.length + 2 * CONTEXT))),
  ]);
  if (id !== current) return;
  const total = performance.now() - t;
  const gets = ix.stats.gets - before.gets;
  const bytes = ix.stats.bytes - before.bytes;
  summary.textContent = r.matches
    ? `${fmt(r.matches)} ${r.matches === 1 ? 'match' : 'matches'} in ${fmt(r.files)} ${r.files === 1 ? 'file' : 'files'}`
      + ` · search ${Math.round(searched)} ms, with names and notes ${Math.round(total)} ms · ${gets} GETs, ${kb(bytes)}`
    : `No match · ${Math.round(searched)} ms · ${gets} GETs, ${kb(bytes)}`;

  results.replaceChildren(...r.positions.map((p, i) => {
    const f = files[i];
    const name = splitFile(f.name);
    const from = Math.max(f.start, p - CONTEXT);
    const shown = [...texts[i].subarray(from - Math.max(0, p - CONTEXT))]
      .slice(0, f.end - from)
      .map((n, k) => (from + k >= p && from + k < p + notes.length ? el('b', {}, midiToName(n)) : midiToName(n)));
    const line = el('p', { className: 'notes' });
    shown.forEach((n, k) => line.append(...(k ? [' ', n] : [n])));
    return el('article', { className: 'card' },
      el('h3', {}, f.title || name.title),
      el('p', { className: 'muted' }, `${f.name} · note ${fmt(f.offset + 1)} of ${fmt(f.end - f.start)}`),
      line);
  }));
}

$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  run($('q').value);
});
for (const [label, q] of EXAMPLES) {
  $('examples').append(' ', el('button', {
    type: 'button', textContent: label, onclick: () => { $('q').value = q; run(q); },
  }));
}
const q = new URLSearchParams(location.search).get('q');
if (q) {
  $('q').value = q;
  run(q);
}
