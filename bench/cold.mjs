// What a visitor who makes 1 or 2 searches waits for: a new Database (an
// empty cache) for each visitor, then 2 queries. Reads a database that
// sqlite-convert wrote (reduck-sqlite) or that the service published.
//
//   node bench/cold.mjs --dir data/convert/<variant> --db <id>       a local folder, with a network model
//   node bench/cold.mjs --base https://<host> --db <id>              HTTP: CloudFront, a public bucket
//   node bench/cold.mjs --s3 <bucket> --region <r> --db <id>          S3, signed (AWS_PROFILE)
//
// Options: --queries (data/bench/shard-queries.json), --visitors (40),
// --reader (the built reader's index.js), --zstd js|native, --range-gets
// (with a framed manifest), --warm-tokens (one query per token, all at once,
// before the phrase query), --wait-interior (open waits for the interior
// bundles, as the reader did before 2026-11-19), --manifest <key> (read
// that manifest of the database, not the one its head names), --read-ahead (0), --parallel (8), --latency (ms
// per GET, 9) and --mbps (megabytes per second of the link, 57) for --dir,
// --out.

import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { open as openFile, readFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';
import zlib from 'node:zlib';
import { readFileSync, writeFileSync } from 'node:fs';

const { values: a } = parseArgs({
  options: {
    dir: { type: 'string' }, base: { type: 'string' }, s3: { type: 'string' }, region: { type: 'string' },
    db: { type: 'string' }, prefix: { type: 'string', default: 'sqlite/v1' },
    queries: { type: 'string', default: 'data/bench/shard-queries.json' },
    visitors: { type: 'string', default: '40' },
    reader: { type: 'string', default: '../reduck-sqlite/reader/dist/index.js' },
    zstd: { type: 'string', default: 'js' },
    'range-gets': { type: 'boolean', default: false },
    'warm-tokens': { type: 'boolean', default: false },
    'wait-interior': { type: 'boolean', default: false },
    manifest: { type: 'string' },
    latency: { type: 'string', default: '9' }, mbps: { type: 'string', default: '57' },
    parallel: { type: 'string', default: '8' },
    'read-ahead': { type: 'string', default: '0' },
    out: { type: 'string' },
  },
});
if (!a.db || !(a.dir || a.base || a.s3)) throw new Error('give --db and one of --dir, --base, --s3');
const R = await import(pathToFileURL(resolve(a.reader)).href);
const sleep = (ms) => new Promise((r) => setTimeout(r, Math.max(0, ms)));

/** A folder of the object store, with a link of `latency` ms per GET and `mbps` MB/s shared by all GETs. */
function folderStore(dir, latency, mbps) {
  let free = 0; // when the link has sent what it was asked
  const wait = async (bytes) => {
    const now = performance.now();
    free = Math.max(now, free) + (bytes / (mbps * 1e6)) * 1000;
    await sleep(free + latency - now);
  };
  const missing = (e) => { if (e.code === 'ENOENT') return undefined; throw e; };
  return {
    async get(key) {
      const b = await readFile(join(dir, key)).catch(missing);
      if (b) await wait(b.length);
      return b && new Uint8Array(b.buffer, b.byteOffset, b.length);
    },
    async getRange(key, off, n) {
      const f = await openFile(join(dir, key)).catch(missing);
      if (!f) return undefined;
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

// temporary credentials of AWS_PROFILE, read once, kept in memory only
const creds = a.s3 ? JSON.parse(execFileSync('aws', ['configure', 'export-credentials', '--format', 'process'], { encoding: 'utf8' })) : undefined;

function baseStore() {
  if (a.dir) return folderStore(a.dir, Number(a.latency), Number(a.mbps));
  if (a.base) return R.httpStore(a.base);
  return R.s3Store({ bucket: a.s3, region: a.region, credentials: { accessKeyId: creds.AccessKeyId, secretAccessKey: creds.SecretAccessKey, sessionToken: creds.SessionToken } });
}

// --manifest: a head that names that manifest, made once
const headKey = `${a.prefix}/${a.db}/head.json`;
let head;
if (a.manifest) {
  const z = await baseStore().get(a.manifest);
  if (!z) throw new Error(`${a.manifest}: not found`);
  head = new TextEncoder().encode(JSON.stringify({
    format: 1, database_id: a.db, generation: 1,
    snapshot: { key: a.manifest, sha256: createHash('sha256').update(z).digest('hex'), size: z.length, txid: 1 },
  }));
}

function makeStore() {
  const store = baseStore();
  if (!head) return store;
  return { get: (key) => (key === headKey ? Promise.resolve(head) : store.get(key)), getRange: store.getRange?.bind(store) };
}

const COUNT = 'SELECT count(*) AS n FROM m WHERE m MATCH ?';
const queries = JSON.parse(readFileSync(a.queries, 'utf8'));
// visitors: an even spread over the queries; each also makes the query after its own
const n = Number(a.visitors);
const picks = Array.from({ length: n }, (_, i) => Math.floor((i * queries.length) / n));
const options = {
  readAhead: Number(a['read-ahead']), parallel: Number(a.parallel), rangeGets: a['range-gets'],
  waitInterior: a['wait-interior'],
  zstd: a.zstd === 'native' ? (z, size) => new Uint8Array(zlib.zstdDecompressSync(z, { maxOutputLength: size || undefined })) : undefined,
};
const ms = (t) => performance.now() - t;
const stats = (db) => ({ gets: db.stats.chunks?.gets ?? 0, bytes: db.stats.chunks?.bytes ?? 0, runs: db.stats.retries });
const diff = (x, y) => ({ gets: y.gets - x.gets, bytes: y.bytes - x.bytes, runs: y.runs - x.runs });

async function search(db, q) {
  if (a['warm-tokens']) {
    const tokens = [...new Set(q.phrase.slice(1, -1).split(' '))];
    await Promise.all(tokens.map((t) => db.all(COUNT, [`"${t}"`])));
  }
  const files = Number((await db.all(COUNT, [q.phrase]))[0].n);
  if (files !== q.files) throw new Error(`${q.label}: ${files} files, expected ${q.files}`);
}

// the WASM module, once per page load: not part of a visitor's time here
let t = performance.now();
await R.loadSQLite();
const wasm_ms = ms(t);

const rows = [];
for (const [i, k] of picks.entries()) {
  const store = makeStore();
  t = performance.now();
  const db = await R.Database.open({ store, prefix: a.prefix, databaseId: a.db, ...options });
  const open_ms = ms(t);
  const s0 = stats(db);
  const q1 = queries[k], q2 = queries[(k + 1) % queries.length];
  t = performance.now();
  await search(db, q1);
  const first_ms = ms(t);
  const s1 = stats(db);
  t = performance.now();
  await search(db, q2);
  const second_ms = ms(t);
  const s2 = stats(db);
  db.close();
  const row = { visitor: i, label: q1.label, open_ms, first_ms, second_ms, open: s0, first: diff(s0, s1), second: diff(s1, s2) };
  rows.push(row);
  if (i < 3 || i % 10 === 0) console.log(JSON.stringify(row));
}

const pick = (f) => rows.map(f).sort((x, y) => x - y);
const med = (f) => { const v = pick(f); return v[Math.floor(v.length / 2)]; };
const p90 = (f) => { const v = pick(f); return v[Math.floor(v.length * 0.9)]; };
const summary = {
  wasm_ms,
  open_ms: med((r) => r.open_ms), first_ms: med((r) => r.first_ms), second_ms: med((r) => r.second_ms),
  open_plus_first_ms: med((r) => r.open_ms + r.first_ms), open_plus_first_p90: p90((r) => r.open_ms + r.first_ms),
  open_gets: med((r) => r.open.gets), open_mb: med((r) => r.open.bytes) / 1e6,
  first_gets: med((r) => r.first.gets), first_runs: med((r) => r.first.runs), first_mb: med((r) => r.first.bytes) / 1e6,
  visitor_mb: med((r) => r.open.bytes + r.first.bytes + r.second.bytes) / 1e6,
};
console.log(JSON.stringify(summary));
if (a.out) writeFileSync(a.out, JSON.stringify({ args: a, summary, rows }, null, 1));
