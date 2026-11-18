// Runs the FTS5 queries of bench/fts5.py on a sqlite4all database read
// straight from its object store, with no server: the reader of
// reduck-sqlite (SQLite in WebAssembly, GETs of the chunks it needs).
//
//   node bench/reader.mjs --base https://<bucket>.s3.<region>.amazonaws.com --db <database id>
//   node bench/reader.mjs --base https://<cloudfront domain> --db <database id>
//
// Options: --prefix (sqlite/v1), --queries (data/bench/shard-queries.json,
// written from fts5.py), --out (data/bench/reader-shard.json), --reader
// (the built reader: ../reduck-sqlite/reader/dist/index.js), --repeat (3),
// --read-ahead (the reader's readAhead, 16), --cache-mib (its cacheBytes, 256),
// --parallel (its parallel, 8), --prefetch (GET every chunk after open; give a
// cache larger than the database), --warm-tokens (before each query, one
// query per token of its phrase, all at once: their chunks download in
// parallel, then the phrase query reads them from the cache; cold_ms
// includes them).
//
// For each query: the first run (cold: it GETs the chunks it reads), then
// the best of --repeat runs (warm: the chunks are in the cache). The pages
// SQLite reads are counted by wrapping the reader's page(): chunks_read is
// the number of chunks (1 MiB each) of the leaf pages the query reads;
// gets counts the chunks fetched, read-ahead included.

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const { values: args } = parseArgs({
  options: {
    base: { type: 'string' },
    db: { type: 'string' },
    prefix: { type: 'string', default: 'sqlite/v1' },
    queries: { type: 'string', default: 'data/bench/shard-queries.json' },
    out: { type: 'string', default: 'data/bench/reader-shard.json' },
    reader: { type: 'string', default: '../reduck-sqlite/reader/dist/index.js' },
    repeat: { type: 'string', default: '3' },
    'read-ahead': { type: 'string', default: '16' },
    'cache-mib': { type: 'string', default: '256' },
    parallel: { type: 'string', default: '8' },
    prefetch: { type: 'boolean', default: false },
    'warm-tokens': { type: 'boolean', default: false },
  },
});
if (!args.base || !args.db) throw new Error('give --base and --db');

const { Database, httpStore } = await import(pathToFileURL(resolve(args.reader)).href);
const COUNT = 'SELECT count(*) AS n FROM m WHERE m MATCH ?';
const queries = JSON.parse(readFileSync(args.queries, 'utf8'));

const ms = (t) => Number(process.hrtime.bigint() - t) / 1e6;
const delta = (a, b) => ({
  gets: (b.chunks?.gets ?? 0) - (a.chunks?.gets ?? 0),
  bytes: (b.chunks?.bytes ?? 0) - (a.chunks?.bytes ?? 0),
  retries: b.retries - a.retries,
  evicted: (b.chunks?.evicted ?? 0) - (a.chunks?.evicted ?? 0),
});

let t = process.hrtime.bigint();
const db = await Database.open({
  store: httpStore(args.base), prefix: args.prefix, databaseId: args.db,
  readAhead: Number(args['read-ahead']), cacheBytes: Number(args['cache-mib']) * 2 ** 20,
  parallel: Number(args.parallel),
});
const open = { ms: ms(t), kind: db.kind, txid: db.txid, ...delta({ retries: 0 }, db.stats),
  read_ahead: Number(args['read-ahead']), cache_mib: Number(args['cache-mib']), parallel: Number(args.parallel) };

// the pages SQLite reads (a private field of Database: this is a benchmark)
const file = db.chunks;
const read = new Set();
if (file) {
  const page = file.page.bind(file);
  file.page = (pgno) => {
    const data = page(pgno);
    if (data) read.add(pgno);
    return data;
  };
  open.chunks = file.m.chunks.length;
  open.pages = file.m.pages;
  open.stored_bytes = file.m.chunks.reduce((n, c) => n + c.size, 0);
}
// GETs of each chunk (private methods of the reader: this is a benchmark)
const fetches = new Map();
if (file) {
  const fetch = file.fetch.bind(file);
  file.fetch = (hash, size, pages) => {
    fetches.set(hash, (fetches.get(hash) ?? 0) + 1);
    return fetch(hash, size, pages);
  };
}
if (file && args.prefetch) {
  t = process.hrtime.bigint();
  const before = db.stats;
  await Promise.all(file.m.chunks.map((ch, c) => (ch.pages.length ? file.load(c, false) : null)));
  open.prefetch = { ms: ms(t), ...delta(before, db.stats) };
}
const pagesRead = () => {
  const leaf = [...read].filter((p) => !file.interior.has(p));
  return { pages_read: read.size, interior_pages_read: read.size - leaf.length,
    chunks_read: new Set(leaf.map((p) => file.m.chunkOf[p])).size };
};
console.log(JSON.stringify({ open }));

const rows = [];
for (const q of queries) {
  const before = db.stats;
  read.clear();
  t = process.hrtime.bigint();
  if (args['warm-tokens']) {
    const tokens = [...new Set(q.phrase.slice(1, -1).split(' '))];
    await Promise.all(tokens.map((tok) => db.all(COUNT, [`"${tok}"`])));
  }
  const files = Number((await db.all(COUNT, [q.phrase]))[0].n);
  const cold = ms(t);
  const fetched = { ...delta(before, db.stats), ...(file ? pagesRead() : {}) };
  if (files !== q.files) throw new Error(`${q.label}: ${files} files, expected ${q.files}`);
  let warm = Infinity;
  for (let i = 0; i < Number(args.repeat); i++) {
    t = process.hrtime.bigint();
    await db.all(COUNT, [q.phrase]);
    warm = Math.min(warm, ms(t));
  }
  const row = { label: q.label, notes: q.notes, files, cold_ms: cold, warm_ms: warm, ...fetched };
  rows.push(row);
  console.log(JSON.stringify(row));
}
db.close();

const median = (v) => { const s = [...v].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
const p90 = (v) => [...v].sort((a, b) => a - b)[Math.floor(v.length * 0.9)];
const summary = {};
for (const k of ['cold_ms', 'warm_ms', 'gets', 'bytes', 'retries', 'chunks_read', 'pages_read']) {
  if (!(k in rows[0])) continue;
  const v = rows.map((r) => r[k]);
  summary[k] = { median: median(v), p90: p90(v), max: Math.max(...v), total: v.reduce((a, b) => a + b, 0) };
}
const counts = [...fetches.values()];
const total = { gets: counts.reduce((a, b) => a + b, 0), distinct_chunks: counts.length,
  fetched_twice_or_more: counts.filter((n) => n > 1).length, max_fetches_of_one_chunk: Math.max(0, ...counts),
  bytes: db.stats.chunks?.bytes, evicted: db.stats.chunks?.evicted };
console.log(JSON.stringify({ open, summary, total }, null, 1));
writeFileSync(args.out, JSON.stringify({ open, summary, total, rows }, null, 1));
