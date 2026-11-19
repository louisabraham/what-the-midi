# Search on SQLite in S3, with no server

`index.html` searches a melody in the browser: the reduck-sqlite reader
(SQLite in WebAssembly) reads an FTS5 index of 4-note tokens from a public
S3 bucket, with range GETs of 64 KiB frames.

```sh
node demo/sqlite-s3/server.mjs        # serves the page and the reader: http://127.0.0.1:8770
```

The reader is `../reduck-sqlite/reader` (or `READER=…`), built with
`npm run build:ts`. The page takes `?base=<bucket URL>&db=<database id>`.

The page reads the whole collection (`?db=what-the-midi-all`, the default)
or one shard (`?db=what-the-midi`). Each database has its rows numbered by
copies, most copied first, and a `files` table of names and titles.

```sh
uv run demo/sqlite-s3/build_db.py data/db-all data/demo-all.sqlite        # 4.19 M melodies: 35 min, 6.1 GB
sqlite-convert -db data/demo-all.sqlite -to s3://<bucket> -region <region> -id what-the-midi-all -preset reader -optimize=false
uv run bench/fts5.py shard data/db-all data/shard                          # or one shard
uv run demo/sqlite-s3/build_db.py data/db-all data/shard/demo.sqlite --shard data/shard
```

Measured in Chrome from a client in Europe (S3 eu-west-3, about 35 ms per
GET), median of a few searches. The whole collection has one interior
bundle (`-bundle-max`, reduck-sqlite `docs/convert.md`); the shard was
measured on 2026-11-19, with every interior page in bundles:

| | One shard (217,143 files) | Whole collection (4,190,187 files) |
| --- | --- | --- |
| Ready (open, then the number of files) | 300 ms, 6 GETs, 1.2 MB | 360 to 410 ms, 3 GETs, 0.56 MB + 1.45 MB of manifest |
| A search, empty cache | 350 ms, 16 GETs, 0.5 MB | 460 to 660 ms, 32 to 87 GETs, 1.4 to 3.9 MB |

The bucket allows `s3:GetObject` on `sqlite/v1/*` to anyone, and a CORS
rule for GET with `Range`.
