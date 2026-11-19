// The search: notes -> 4-note tokens -> an FTS5 phrase query, run by the
// reduck-sqlite reader on the database in the bucket.
//
// A search is 3 steps, each a set of statements run at once, so that the
// pages they lack are fetched in parallel:
// 1. one count per token of the phrase: their pages of the term index and
//    of the doclists (a phrase query alone would fetch them one by one);
// 2. the phrase: the number of files and the first 10 rows (the rows are
//    numbered by copies, most copied first);
// 3. the name of each of those files.
//
// ?base=<url of the bucket>&db=<database id> to read another database.

import { Database, httpStore } from '/reader/index.js';

const params = new URLSearchParams(location.search);
const BASE = params.get('base') ?? 'https://reduck-reader-bench-727068700427.s3.eu-west-3.amazonaws.com';
const DB = params.get('db') ?? 'what-the-midi-all';
const DATABASES = [['what-the-midi-all', 'the whole collection'], ['what-the-midi', 'one shard (217,143 melodies)']];
const PREFIX = 'sqlite/v1';
const LIMIT = 10;
const EXAMPLES = [
  ['Für Elise', 'E5 D#5 E5 D#5 E5 B4 D5 C5 A4'],
  ['Ode to Joy', 'E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4'],
  ['Happy Birthday', 'G4 G4 A4 G4 C5 B4 G4 G4 A4 G4 D5 C5'],
  ['Twinkle', 'C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4'],
  ['Beethoven 5', 'G4 G4 G4 D#4 F4 F4 F4 D4'],
];

const $ = (id) => document.getElementById(id);
const fmt = (n) => Number(n).toLocaleString('en');
const ms = (x) => `${Math.round(x)} ms`;
const size = (b) => (b < 1e6 ? `${(b / 1e3).toFixed(0)} KB` : `${(b / 1e6).toFixed(2)} MB`);
const el = (tag, props = {}, ...children) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...children);
  return e;
};

// ---------------------------------------------------------------- notes

const STEPS = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
const nameOf = (p) => `${NAMES[p % 12]}${Math.floor(p / 12) - 1}`;

/** "E5 D#5 Bb4 60" -> MIDI numbers; a note without octave goes nearest to the note before (C4 = 60). */
function parseNotes(text) {
  const out = [];
  for (const tok of text.split(/[\s,]+/).filter(Boolean)) {
    if (/^\d+$/.test(tok)) {
      out.push(Number(tok));
      continue;
    }
    const m = /^([A-Ga-g])([#♯b♭]?)(-?\d+)?$/.exec(tok);
    if (!m) throw new Error(`"${tok}" is not a note (C4, D#5, Bb3 or a MIDI number)`);
    const pc = (STEPS[m[1].toUpperCase()] + ({ '#': 1, '♯': 1, b: -1, '♭': -1 }[m[2]] ?? 0) + 12) % 12;
    let p;
    if (m[3] !== undefined) p = (Number(m[3]) + 1) * 12 + pc;
    else {
      const prev = out.at(-1) ?? 60;
      p = prev - ((prev - pc) % 12 + 12) % 12;
      if (prev - p > 6) p += 12;
    }
    if (p < 0 || p > 127) throw new Error(`"${tok}" is out of the MIDI range`);
    out.push(p);
  }
  return out;
}

/** One token for each 4 consecutive pitches: 8 hex digits ("3c3e4043" is C4 D4 E4 G4). */
function tokens(pitches) {
  const hex = (p) => p.toString(16).padStart(2, '0');
  const out = [];
  for (let i = 0; i + 4 <= pitches.length; i++) out.push(pitches.slice(i, i + 4).map(hex).join(''));
  return out;
}

// ---------------------------------------------------------------- database

let db;
let opening;
let searches = 0;
const stats = () => ({ gets: db.stats.chunks?.gets ?? 0, bytes: db.stats.chunks?.bytes ?? 0 });

async function openDatabase() {
  db?.close();
  db = undefined;
  const t = performance.now();
  // readAhead 0: lookups, not scans (the measured setting, bench/cold.mjs)
  const d = await Database.open({ store: httpStore(BASE), prefix: PREFIX, databaseId: DB, readAhead: 0 });
  // the rows are numbered 1 to n: max(id) reads one leaf, count(*) the whole table
  const total = (await d.all('SELECT max(id) AS n FROM files'))[0].n;
  const took = performance.now() - t;
  db = d;
  const s = stats();
  $('about').textContent = `${fmt(total)} files. Ready in ${ms(took)}: the head, the manifest, then ${s.gets} GETs`
    + ` of pages, ${size(s.bytes)} (the top interior pages of the B-trees, page 1, the last page of files), from ${BASE}`;
  $('about').className = 'muted';
  return d;
}

function reopen() {
  $('about').textContent = 'Opening the database…';
  opening = openDatabase().catch((e) => {
    $('about').textContent = `Cannot open ${DB} at ${BASE}: ${e.message}`;
    $('about').className = 'error';
    throw e;
  });
  return opening;
}

const COUNT = 'SELECT count(*) AS n FROM m WHERE m MATCH ?';

async function search(text) {
  const pitches = parseNotes(text);
  if (pitches.length < 4) throw new Error('give at least 4 notes');
  await opening;
  const cold = searches === 0;
  const phrase = `"${tokens(pitches).join(' ')}"`;
  const s0 = stats();
  const t0 = performance.now();
  await Promise.all([...new Set(tokens(pitches))].map((t) => db.all(COUNT, [`"${t}"`])));
  const t1 = performance.now();
  const [count, first] = await Promise.all([
    db.all(COUNT, [phrase]),
    db.query(`SELECT rowid FROM m WHERE m MATCH ? LIMIT ${LIMIT}`, [phrase]),
  ]);
  const t2 = performance.now();
  const files = await Promise.all(first.rows.map(([id]) =>
    db.all('SELECT id, name, title, copies FROM files WHERE id = ?', [id]).then((r) => r[0])));
  const t3 = performance.now();
  const s1 = stats();
  searches++;
  return {
    pitches, n: Number(count[0].n), files, cold,
    times: { tokens: t1 - t0, phrase: t2 - t1, names: t3 - t2, total: t3 - t0 },
    gets: s1.gets - s0.gets, bytes: s1.bytes - s0.bytes,
  };
}

// ---------------------------------------------------------------- page

let current = 0;

async function run(text) {
  const id = ++current;
  $('summary').textContent = 'Searching…';
  $('summary').className = 'stats';
  $('results').replaceChildren();
  try {
    const r = await search(text);
    if (id !== current) return;
    const t = r.times;
    $('summary').textContent = `${fmt(r.n)} files contain ${r.pitches.map(nameOf).join(' ')}. `
      + `${ms(t.total)}: tokens ${ms(t.tokens)}, phrase ${ms(t.phrase)}, names ${ms(t.names)}; `
      + `${r.gets} GETs, ${size(r.bytes)}.`;
    $('results').replaceChildren(...r.files.map((f) => el('div', { className: 'card' },
      el('div', {}, el('h3', { textContent: f.title || f.name }), f.title ? el('p', { textContent: f.name }) : ''),
      el('span', { className: 'copies', textContent: `${fmt(f.copies)} ${Number(f.copies) === 1 ? 'copy' : 'copies'}` }))));
    if (r.n > r.files.length) $('results').append(el('p', { className: 'muted', textContent: `The ${r.files.length} most copied of ${fmt(r.n)}.` }));
    $('log').prepend(el('tr', {},
      el('td', { textContent: text.length > 40 ? `${text.slice(0, 40)}…` : text }),
      el('td', { textContent: r.cold ? 'empty' : 'warm' }), el('td', { textContent: fmt(r.n) }),
      ...[t.total, t.tokens, t.phrase, t.names].map((x) => el('td', { textContent: ms(x) })),
      el('td', { textContent: String(r.gets) }), el('td', { textContent: size(r.bytes) })));
  } catch (e) {
    if (id !== current) return;
    $('summary').textContent = e.message;
    $('summary').className = 'stats error';
  }
}

for (const [id, label] of DATABASES) {
  $('databases').append(id === DB ? el('b', { textContent: label }) : el('a', { href: `?db=${id}`, textContent: label }));
}
for (const [name, notes] of EXAMPLES) {
  $('examples').append(el('button', { type: 'button', textContent: name, onclick: () => { $('q').value = notes; run(notes); } }));
}
$('form').addEventListener('submit', (e) => {
  e.preventDefault();
  const text = $('q').value.trim();
  if (text) run(text);
});
$('fresh').addEventListener('click', () => {
  searches = 0;
  $('summary').textContent = '';
  $('results').replaceChildren();
  reopen();
});
reopen();
