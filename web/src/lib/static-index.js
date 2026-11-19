// Search a melody in a static index (whatthemidi/static.py) with HTTP GETs
// only: S3, a CDN, or any static host that answers range requests. No
// server, no dependency: it runs in a browser and in Node.
//
//   const index = await StaticIndex.open(httpStore('https://<bucket>.s3.<region>.amazonaws.com/<prefix>'));
//   const r = await index.search([76, 75, 76, 75, 76, 71, 74, 72, 69]);
//   r.matches     occurrences of the sequence
//   r.files       files that contain it
//   r.positions   start in the text of the first occurrences
//
// A search goes down the tree from the root (in memory after open) to the
// leaves: one round of GETs per level, the node of the first and of the
// last match in parallel. It reads the text only to check the one key that
// can be shorter than the query, and the dup bytes only when the matches
// fill more than the 2 leaves at their ends.

const LESS = -1;
const EQUAL = 0;
const GREATER = 1;
const UNKNOWN = 2;

/** GETs <base>/<key>: a public bucket, a CDN, a static server. */
export function httpStore(base, { headers = {}, fetch: f = (...a) => globalThis.fetch(...a) } = {}) {
  base = base.replace(/\/+$/, '');
  const body = async (res, key) => {
    if (!res.ok) throw new Error(`GET ${key}: ${res.status} ${res.statusText}`);
    return new Uint8Array(await res.arrayBuffer());
  };
  return {
    async get(key) {
      return body(await f(`${base}/${key}`, { headers }), key);
    },
    async getRange(key, off, n) {
      const res = await f(`${base}/${key}`, { headers: { ...headers, Range: `bytes=${off}-${off + n - 1}` } });
      const b = await body(res, key);
      return res.status === 206 ? b : b.subarray(off, off + n); // a server that ignores Range
    },
  };
}

function u32(bytes, off, n) {
  const at = bytes.byteOffset + off;
  if (at % 4 === 0) return new Uint32Array(bytes.buffer, at, n);
  return new Uint32Array(bytes.slice(off, off + 4 * n).buffer);
}

/** The keys of a node: c[i] notes of the key before, then len[i] notes from the node. */
function keys(bytes, off, n, c, len) {
  const out = new Array(n);
  let prev = new Uint8Array(0);
  for (let i = 0; i < n; i++) {
    const k = new Uint8Array(c[i] + len[i]);
    k.set(prev.subarray(0, c[i]));
    k.set(bytes.subarray(off, off + len[i]), c[i]);
    off += len[i];
    out[i] = prev = k;
  }
  return out;
}

function internalNode(b) {
  const [n, first] = u32(b, 0, 2);
  let o = 8;
  const arr = () => { const a = u32(b, o, n); o += 4 * n; return a; };
  const pos = arr(), part = arr(), off = arr(), size = arr();
  const c = b.subarray(o, o + n);
  const len = b.subarray(o + n, o + 2 * n);
  return { n, first, pos, part, off, size, keys: keys(b, o + 2 * n, n, c, len) };
}

function leafNode(b, first) {
  const n = u32(b, 0, 1)[0];
  const pos = u32(b, 4, n);
  let o = 4 + 4 * n;
  const c = b.subarray(o, o + n);
  const len = b.subarray(o + n, o + 2 * n);
  const dup = b.subarray(o + 2 * n, o + 3 * n);
  return { n, first, pos, dup, keys: keys(b, o + 3 * n, n, c, len) };
}

/** Compares the suffix that starts with key to the query, on the first q.length notes. */
function cmp(key, q) {
  const n = Math.min(key.length, q.length);
  for (let i = 0; i < n; i++) {
    if (key[i] !== q[i]) return key[i] < q[i] ? LESS : GREATER;
  }
  return key.length >= q.length ? EQUAL : UNKNOWN;
}

export class StaticIndex {
  constructor(store, prefix) {
    this.store = store;
    this.prefix = prefix ? prefix.replace(/\/*$/, '/') : '';
    this.nodes = new Map(); // "level/index" -> Promise of a node
    this.stats = { gets: 0, bytes: 0, rounds: 0, checks: 0 };
  }

  /** Reads manifest.json and root.bin (1 round of 2 GETs). */
  static async open(store, { prefix = '' } = {}) {
    const ix = new StaticIndex(store, prefix);
    const [m, root] = await ix.round([ix.get('manifest.json'), ix.get('root.bin')]);
    ix.m = JSON.parse(new TextDecoder().decode(m));
    if (ix.m.format !== 'what-the-midi-static' || ix.m.version !== 1) {
      throw new Error(`not a static index of version 1: ${ix.m.format} ${ix.m.version}`);
    }
    ix.root = internalNode(root);
    return ix;
  }

  get manifest() {
    return this.m;
  }

  async get(key) {
    const b = await this.store.get(this.prefix + key);
    if (!b) throw new Error(`no object ${this.prefix + key}`);
    this.stats.gets++;
    this.stats.bytes += b.length;
    return b;
  }

  async range(key, off, n) {
    const b = await this.store.getRange(this.prefix + key, off, n);
    if (!b || b.length !== n) throw new Error(`${this.prefix + key}: ${b?.length} bytes at ${off}, expected ${n}`);
    this.stats.gets++;
    this.stats.bytes += n;
    return b;
  }

  /** Waits for GETs sent at the same time: one round trip. */
  async round(promises) {
    const gets = this.stats.gets;
    const out = await Promise.all(promises);
    if (this.stats.gets > gets) this.stats.rounds++;
    return out;
  }

  /** n bytes of the text or of dup from rank or position start. */
  async bytes(kind, start, n) {
    const { part_bytes: size, parts } = this.m[kind];
    const pieces = [];
    for (let s = start; s < start + n;) {
      const k = Math.floor(s / size);
      const len = Math.min(start + n - s, (k + 1) * size - s);
      pieces.push(this.range(parts[k], s - k * size, len));
      s += len;
    }
    const got = await Promise.all(pieces);
    if (got.length === 1) return got[0];
    const out = new Uint8Array(n);
    let o = 0;
    for (const g of got) { out.set(g, o); o += g.length; }
    return out;
  }

  /** Child i of an internal node; its nodes are at level `level`. */
  child(node, i, level) {
    const index = node.first + i;
    const id = `${level}/${index}`;
    let p = this.nodes.get(id);
    if (!p) {
      const part = this.m.levels[level].parts[node.part[i]];
      p = this.range(part, node.off[i], node.size[i]).then((b) =>
        level === 0 ? leafNode(b, index * this.m.leaf) : internalNode(b));
      p.catch(() => this.nodes.delete(id));
      this.nodes.set(id, p);
    }
    return p;
  }

  /** cmp of the suffix at pos, with its text when its key is shorter than the query. */
  compare(pos, key, q, checked) {
    const r = cmp(key, q);
    if (r !== UNKNOWN) return r;
    let p = checked.get(pos);
    if (p === undefined) {
      this.stats.checks++;
      const n = Math.min(q.length, this.m.notes - pos);
      p = this.round([this.bytes('text', pos, n)]).then(([t]) => {
        const v = cmp(t, q);
        return v === UNKNOWN ? LESS : v; // a suffix shorter than the query comes first
      });
      checked.set(pos, p);
    }
    return p;
  }

  /** The first entry of the node for which test(compare) is true (or node.n). */
  async first(node, q, test, checked) {
    let lo = 0;
    let hi = node.n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      let r = this.compare(node.pos[mid], node.keys[mid], q, checked);
      if (typeof r !== 'number') r = await r;
      if (test(r)) hi = mid;
      else lo = mid + 1;
    }
    return lo;
  }

  /**
   * Searches a sequence of note values (for the exact mode: MIDI numbers).
   * Returns {matches, files, lo, hi, positions}: [lo, hi) is the range of
   * ranks of the suffixes that start with the sequence.
   */
  async search(notes, { limit = 10 } = {}) {
    const q = Uint8Array.from(notes);
    if (q.length < 1 || q.length > this.m.cap) throw new RangeError(`1 to ${this.m.cap} notes`);
    const checked = new Map();
    const notLess = (r) => r !== LESS;
    const greater = (r) => r === GREATER;

    let lo = this.root;
    let hi = this.root;
    for (let level = this.m.levels.length - 1; level >= 0; level--) {
      const a = Math.max(0, (await this.first(lo, q, notLess, checked)) - 1);
      const b = Math.max(0, (await this.first(hi, q, greater, checked)) - 1);
      [lo, hi] = await this.round([this.child(lo, a, level), this.child(hi, b, level)]);
    }
    const loLeaf = lo;
    const hiLeaf = hi;
    const start = loLeaf.first + (await this.first(loLeaf, q, notLess, checked));
    const end = hiLeaf.first + (await this.first(hiLeaf, q, greater, checked));

    // files = matches - #{rank in [start, end) : dup >= m}
    const m = q.length;
    let dups = 0;
    const loEnd = loLeaf.first + loLeaf.n;
    for (let r = start; r < Math.min(end, loEnd); r++) if (loLeaf.dup[r - loLeaf.first] >= m) dups++;
    if (hiLeaf.first !== loLeaf.first) {
      for (let r = Math.max(start, hiLeaf.first); r < end; r++) if (hiLeaf.dup[r - hiLeaf.first] >= m) dups++;
      if (hiLeaf.first > loEnd && start < end) {
        const [d] = await this.round([this.bytes('dup', loEnd, hiLeaf.first - loEnd)]);
        for (let i = 0; i < d.length; i++) if (d[i] >= m) dups++;
      }
    }

    const positions = [];
    for (let r = start; r < end && positions.length < limit; r++) {
      const leaf = r < loEnd ? loLeaf : r >= hiLeaf.first ? hiLeaf : null;
      if (!leaf) break;
      positions.push(leaf.pos[r - leaf.first]);
    }
    return { matches: end - start, files: end - start - dups, lo: start, hi: end, positions };
  }

  /** Notes of the text from position start (for the notes around a match). */
  async text(start, n) {
    start = Math.max(0, start);
    const [t] = await this.round([this.bytes('text', start, Math.min(n, this.m.notes - start))]);
    return t;
  }

  /**
   * The file of each position: {file, name, title, start, end, offset}
   * (start and end: the notes of the file in the text). 1 round of GETs of
   * files-<k>.json: the files that have notes in positions
   * [k * files_chunk_notes, (k + 1) * files_chunk_notes). Needs an index
   * built with names.
   */
  async files(positions) {
    const size = this.m.files_chunk_notes;
    if (!size) throw new Error('this index has no file names');
    this.chunks ??= new Map();
    const ks = positions.map((p) => Math.floor(p / size));
    for (const k of new Set(ks)) {
      if (!this.chunks.has(k)) {
        const p = this.get(`files-${String(k).padStart(5, '0')}.json`).then((b) => JSON.parse(new TextDecoder().decode(b)));
        p.catch(() => this.chunks.delete(k));
        this.chunks.set(k, p);
      }
    }
    const chunks = await this.round([...new Set(ks)].map((k) => this.chunks.get(k)));
    const byK = new Map([...new Set(ks)].map((k, i) => [k, chunks[i]]));
    return positions.map((p, i) => {
      const c = byK.get(ks[i]);
      let lo = 0;
      let hi = c.names.length;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (c.starts[mid] <= p) lo = mid;
        else hi = mid;
      }
      return {
        file: c.first + lo, name: c.names[lo], title: c.titles?.[lo],
        start: c.starts[lo], end: c.starts[lo + 1] - 1, offset: p - c.starts[lo],
      };
    });
  }
}
