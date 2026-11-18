// The static index (whatthemidi/static.py, web/src/lib/static-index.js) on
// the queries of bench/fts5.py, with the same stores and the same visitors
// as bench/cold.mjs, to compare with the reader of reduck-sqlite.
//
//   node bench/static.mjs --dir data/static/shard                     a local folder, with a network model
//   node bench/static.mjs --base https://<host>/<prefix>              HTTP: CloudFront, a public bucket
//   node bench/static.mjs --s3 <bucket> --region <r> --prefix <p>     S3, signed GETs (AWS_PROFILE), with the store of the reduck-sqlite reader
//
// Runs:
//   --run visitors   (default) a new index for each visitor (empty cache), open, then 2 queries, like cold.mjs
//   --run queries    each query once on a new index (cold, open not counted), then the best of 3 on the same index (warm)
//   --run check      each query on one index, without a network model: the numbers of files must be the expected ones
//
// Options: --queries (data/bench/shard-queries.json: {phrase, files} or
// {notes, files, matches}), --visitors (40), --latency (ms per GET, 9) and
// --mbps (megabytes per second of the link, 57) for --dir, --reader (the
// built reduck-sqlite reader, for --s3), --out.

import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { open as openFile, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import { StaticIndex, httpStore } from '../web/src/lib/static-index.js';

const { values: a } = parseArgs({
  options: {
    dir: { type: 'string' }, base: { type: 'string' }, s3: { type: 'string' }, region: { type: 'string' },
    prefix: { type: 'string', default: '' },
    queries: { type: 'string', default: 'data/bench/shard-queries.json' },
    run: { type: 'string', default: 'visitors' },
    visitors: { type: 'string', default: '40' },
    latency: { type: 'string', default: '9' }, mbps: { type: 'string', default: '57' },
    reader: { type: 'string', default: '../reduck-sqlite/reader/dist/index.js' },
    out: { type: 'string' },
  },
});
if (!(a.dir || a.base || a.s3)) throw new Error('give one of --dir, --base, --s3');
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

/** A folder of the object store, with a link of `latency` ms per GET and `mbps` MB/s shared by all GETs (as in cold.mjs). */
function folderStore(dir, latency, mbps) {
  let free = 0;
  const wait = async (bytes) => {
    if (!latency && !mbps) return;
    const now = performance.now();
    free = Math.max(now, free) + (bytes / (mbps * 1e6)) * 1000;
    await sleep(free + latency - now);
  };
  return {
    async get(key) {
      const b = await readFile(join(dir, key));
      await wait(b.length);
      return new Uint8Array(b.buffer, b.byteOffset, b.length);
    },
    async getRange(key, off, n) {
      const f = await openFile(join(dir, key));
      try {
        const b = new Uint8Array(n);
        const { bytesRead } = await f.read(b, 0, n, off);
        await wait(bytesRead);
        return b.subarray(0, bytesRead);
      } finally {
        await f.close();
      }
    },
  };
}

let s3Store;
let creds;
if (a.s3) {
  ({ s3Store } = await import(pathToFileURL(resolve(a.reader)).href));
  // temporary credentials of AWS_PROFILE, read once, kept in memory only
  creds = JSON.parse(execFileSync('aws', ['configure', 'export-credentials', '--format', 'process'], { encoding: 'utf8' }));
}
const check = a.run === 'check';

function makeStore() {
  if (a.dir) return folderStore(a.dir, check ? 0 : Number(a.latency), check ? 0 : Number(a.mbps));
  if (a.base) return httpStore(a.base);
  return s3Store({ bucket: a.s3, region: a.region, credentials: { accessKeyId: creds.AccessKeyId, secretAccessKey: creds.SecretAccessKey, sessionToken: creds.SessionToken } });
}

/** The notes of a query of bench/fts5.py: '"3c3e4043 3e404345"' -> [60, 62, 64, 67, 69]. */
function notesOf(q) {
  if (Array.isArray(q.notes)) return q.notes;
  const tokens = q.phrase.replace(/"/g, '').split(' ');
  const hex = (t, i) => parseInt(t.slice(2 * i, 2 * i + 2), 16);
  return [0, 1, 2, 3].map((i) => hex(tokens[0], i)).concat(tokens.slice(1).map((t) => hex(t, 3)));
}

const queries = JSON.parse(readFileSync(a.queries, 'utf8')).map((q, i) => ({ label: q.label ?? `q${i}`, ...q, notes: notesOf(q) }));
const ms = (t) => performance.now() - t;
const snap = (ix) => ({ ...ix.stats });
const diff = (x, y) => ({ gets: y.gets - x.gets, bytes: y.bytes - x.bytes, rounds: y.rounds - x.rounds, checks: y.checks - x.checks });

async function search(ix, q) {
  const r = await ix.search(q.notes);
  if (r.files !== q.files || (q.matches !== undefined && r.matches !== q.matches)) {
    throw new Error(`${q.label} [${q.notes}]: ${r.matches} matches in ${r.files} files, expected ${q.matches} in ${q.files}`);
  }
  return r;
}

const med = (v) => [...v].sort((x, y) => x - y)[Math.floor(v.length / 2)];
const p90 = (v) => [...v].sort((x, y) => x - y)[Math.floor(v.length * 0.9)];
const rows = [];
let summary;

if (a.run === 'visitors') {
  const n = Number(a.visitors);
  const picks = Array.from({ length: n }, (_, i) => Math.floor((i * queries.length) / n));
  for (const [i, k] of picks.entries()) {
    let t = performance.now();
    const ix = await StaticIndex.open(makeStore(), { prefix: a.prefix });
    const open_ms = ms(t);
    const s0 = snap(ix);
    const q1 = queries[k], q2 = queries[(k + 1) % queries.length];
    t = performance.now();
    await search(ix, q1);
    const first_ms = ms(t);
    const s1 = snap(ix);
    t = performance.now();
    await search(ix, q2);
    const second_ms = ms(t);
    const row = { visitor: i, label: q1.label, open_ms, first_ms, second_ms, open: s0, first: diff(s0, s1), second: diff(s1, snap(ix)) };
    rows.push(row);
    if (i < 3 || i % 10 === 0) console.log(JSON.stringify(row));
  }
  const f = (g) => rows.map(g);
  summary = {
    open_ms: med(f((r) => r.open_ms)), first_ms: med(f((r) => r.first_ms)), second_ms: med(f((r) => r.second_ms)),
    open_plus_first_ms: med(f((r) => r.open_ms + r.first_ms)), open_plus_first_p90: p90(f((r) => r.open_ms + r.first_ms)),
    open_gets: med(f((r) => r.open.gets)), open_mb: med(f((r) => r.open.bytes)) / 1e6,
    first_gets: med(f((r) => r.first.gets)), first_rounds: med(f((r) => r.first.rounds)), first_mb: med(f((r) => r.first.bytes)) / 1e6,
    visitor_mb: med(f((r) => r.open.bytes + r.first.bytes + r.second.bytes)) / 1e6,
  };
} else if (a.run === 'queries' || check) {
  const shared = check ? await StaticIndex.open(makeStore(), { prefix: a.prefix }) : null;
  for (const q of queries) {
    const ix = shared ?? (await StaticIndex.open(makeStore(), { prefix: a.prefix }));
    const s0 = snap(ix);
    let t = performance.now();
    const r = await search(ix, q);
    const cold_ms = ms(t);
    const row = { label: q.label, notes: q.notes.length, files: r.files, matches: r.matches, cold_ms, ...diff(s0, snap(ix)) };
    if (!check) {
      let warm = Infinity;
      for (let i = 0; i < 3; i++) {
        t = performance.now();
        await search(ix, q);
        warm = Math.min(warm, ms(t));
      }
      row.warm_ms = warm;
      console.log(JSON.stringify(row));
    }
    rows.push(row);
  }
  summary = { queries: rows.length };
  for (const k of ['cold_ms', 'warm_ms', 'gets', 'bytes', 'rounds', 'checks', 'matches']) {
    if (!(k in rows[0])) continue;
    const v = rows.map((r) => r[k]);
    summary[k] = { median: med(v), p90: p90(v), max: Math.max(...v), total: v.reduce((x, y) => x + y, 0) };
  }
} else {
  throw new Error(`--run visitors, queries or check, not ${a.run}`);
}

console.log(JSON.stringify(summary));
if (a.out) writeFileSync(a.out, JSON.stringify({ args: a, summary, rows }, null, 1));
