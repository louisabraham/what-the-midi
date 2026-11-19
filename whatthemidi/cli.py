import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="what-the-midi", description="Search a melody in a MIDI collection."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("index", help="index a directory of MIDI files")
    p.add_argument("midi_dir")
    p.add_argument("db_dir")
    p.add_argument("-j", "--jobs", type=int, default=None,
                   help="parser processes (default: number of CPUs)")

    p = sub.add_parser("search", help="search a melody in an index")
    p.add_argument("db_dir")
    p.add_argument("query", help="note names (C4 D#4 Bb) or MIDI numbers (60 63 70)")
    p.add_argument("--octave", action="store_true", help="ignore the octave")
    p.add_argument("--transpose", action="store_true", help="search in all keys")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--json", action="store_true", help="print the raw result")

    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("db_dir")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--static", default=None, help="frontend directory (default: web/dist)")

    p = sub.add_parser("static", help="write a static index, for a frontend that reads it from S3")
    p.add_argument("db_dir", help="an index with one part, or the shard of bench/fts5.py")
    p.add_argument("out_dir")
    p.add_argument("--mode", default="exact", choices=["exact", "octave", "transpose", "transpose_octave"])
    p.add_argument("--leaf", type=int, default=4096, help="suffixes per leaf")
    p.add_argument("--fanout", type=int, default=256, help="children per internal node")
    p.add_argument("--root-max", type=int, default=None, help="children of the root (default: fanout)")
    p.add_argument("--key-min", type=int, default=0, help="notes of each key, at least")
    p.add_argument("--part-mib", type=int, default=4096,
                   help="size of the files, at most (default and maximum: 4096, so 1 file per level "
                        "for a shard, the fastest on S3; 64 for GitHub Pages)")
    p.add_argument("--names-from", default=None,
                   help="the index part with names.json, for the shard of bench/fts5.py")

    p = sub.add_parser("serve-static", help="serve a static index on HTTP, with range requests and CORS")
    p.add_argument("out_dir")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8766)

    args = parser.parse_args(argv)

    if args.command == "index":
        from .index import build

        meta = build(args.midi_dir, args.db_dir, jobs=args.jobs)
        print(json.dumps(meta, indent=1))

    elif args.command == "search":
        from .index import open_database
        from .notes import NoteError, midi_to_name, parse_query

        try:
            notes = parse_query(args.query)
            result = open_database(args.db_dir).search(
                notes, args.octave, args.transpose, args.limit
            )
        except (NoteError, ValueError) as e:
            sys.exit(f"error: {e}")
        if args.json:
            print(json.dumps(result, indent=1))
            return
        print("query:", " ".join(midi_to_name(n) for n in notes))
        files = result["files_total"]
        print(f"{result['total']} matches in {files}{'' if result['files_exact'] else '+'} files")
        for r in result["results"]:
            times = ", ".join(f"{p['sec']:.1f}s" for p in r["positions"])
            print(f"{r['occurrences']:4d}  {r['file']}  ({times})")

    elif args.command == "serve":
        from .server import DEFAULT_STATIC, serve

        serve(args.db_dir, args.host, args.port, args.static or DEFAULT_STATIC)

    elif args.command == "static":
        from .static import build, load_names

        names, titles = load_names(args.db_dir, args.names_from)
        stats = build(args.db_dir, args.out_dir, mode=args.mode, leaf=args.leaf, fanout=args.fanout,
                      root_max=args.root_max, key_min=args.key_min,
                      part_bytes=args.part_mib << 20, names=names, titles=titles)
        print(json.dumps(stats, indent=1))

    elif args.command == "serve-static":
        from .static import serve

        serve(args.out_dir, args.host, args.port)


if __name__ == "__main__":
    main()
