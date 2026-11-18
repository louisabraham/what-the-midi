// Serves the demo page and the reduck-sqlite reader (its built dist/, and
// its two dependencies) on 127.0.0.1. The data does not go through this
// server: the page reads the SQLite database from the bucket, with range
// GETs.
//
//   node demo/sqlite-s3/server.mjs [port]        then open http://127.0.0.1:8770
//
// READER: the reader's folder (default ../reduck-sqlite/reader, next to
// this repository), built with `npm run build:ts`.

import { readFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { extname, join, normalize, resolve } from 'node:path';

const here = import.meta.dirname;
const reader = resolve(process.env.READER ?? join(here, '../../../reduck-sqlite/reader'));
const port = Number(process.argv[2] ?? 8770);

const files = {
  '/': join(here, 'index.html'),
  '/app.js': join(here, 'app.js'),
  '/deps/fzstd.mjs': join(reader, 'node_modules/fzstd/esm/index.mjs'),
  '/deps/aws4fetch.mjs': join(reader, 'node_modules/aws4fetch/dist/aws4fetch.esm.mjs'),
};
const dist = join(reader, 'dist');
const types = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.wasm': 'application/wasm', '.map': 'application/json',
};

function fileOf(path) {
  if (files[path]) return files[path];
  if (path.startsWith('/reader/')) {
    const f = normalize(join(dist, path.slice('/reader/'.length)));
    if (f.startsWith(dist + '/')) return f;
  }
  return undefined;
}

createServer(async (req, res) => {
  const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
  const file = fileOf(path);
  try {
    if (!file) throw Object.assign(new Error('not found'), { code: 'ENOENT' });
    const body = await readFile(file);
    res.writeHead(200, { 'Content-Type': types[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-cache' });
    res.end(body);
  } catch (e) {
    res.writeHead(e.code === 'ENOENT' ? 404 : 500, { 'Content-Type': 'text/plain' }).end(`${path}: ${e.message}`);
  }
}).listen(port, '127.0.0.1', () => console.log(`http://127.0.0.1:${port}`));
