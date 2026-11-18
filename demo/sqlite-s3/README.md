# Search on SQLite in S3, with no server

`index.html` searches a melody in the browser: the reduck-sqlite reader
(SQLite in WebAssembly) reads an FTS5 index of 4-note tokens from a public
S3 bucket, with range GETs of 64 KiB frames.

```sh
node demo/sqlite-s3/server.mjs        # serves the page and the reader: http://127.0.0.1:8770
```

The reader is `../reduck-sqlite/reader` (or `READER=…`), built with
`npm run build:ts`. The page takes `?base=<bucket URL>&db=<database id>`.

The database: one shard of the index (217,143 melodies), rows numbered by
copies, with a `files` table of names and titles.

```sh
uv run bench/fts5.py shard data/db-all data/shard
uv run demo/sqlite-s3/build_db.py data/shard data/db-all data/shard/demo.sqlite
sqlite-convert -db data/shard/demo.sqlite -to s3://<bucket> -region <region> -id what-the-midi -preset reader
```

The bucket allows `s3:GetObject` on `sqlite/v1/*` to anyone, and a CORS
rule for GET with `Range`.
