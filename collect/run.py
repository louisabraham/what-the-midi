"""Downloads MIDI collections, removes duplicates, and builds the index.

    uv run collect/run.py list
    uv run collect/run.py download midiman magic_of_midi
    uv run collect/run.py ingest midiman magic_of_midi
    uv run collect/run.py all midiman magic_of_midi   # download + ingest
    uv run collect/run.py stats
    uv run collect/run.py metadata                    # titles from GigaMIDI (by MD5)
    uv run collect/run.py neardup                     # group near duplicates
    uv run collect/run.py build data/db-all

Sources are processed in the order of sources.SOURCES, whatever the order
on the command line: the first source keeps the name of a melody.
"""

import argparse
import json
import shutil
from pathlib import Path

from sources import BY_NAME, SOURCES

ROOT = Path(__file__).resolve().parent.parent / "data"

# disk use per note, measured on the first collections
INDEX_BYTES_PER_NOTE = 24.5
STORE_BYTES_PER_NOTE = 3.0
FREE_MARGIN = 10 * 2**30  # the index must leave 10 GiB free


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def note_budget(data: Path, store: Path, deleted_after: list[Path]) -> int:
    """How many more notes the store can take, so that the index built at
    the end leaves FREE_MARGIN free. Counts as free the archives deleted
    after their ingestion and the old indexes (data/db*), which the build
    replaces."""
    from ingest import connect

    free = shutil.disk_usage(data).free
    free += sum(dir_size(p) for p in deleted_after if p.exists())
    free += sum(dir_size(p) for p in data.glob("db*") if p.is_dir())
    stored = connect(store).execute("SELECT COALESCE(SUM(notes), 0) FROM melodies").fetchone()[0]
    available = free - FREE_MARGIN - stored * INDEX_BYTES_PER_NOTE
    return max(0, int(available / (INDEX_BYTES_PER_NOTE + STORE_BYTES_PER_NOTE)))


def ordered(names):
    unknown = set(names) - set(BY_NAME)
    if unknown:
        raise SystemExit(f"unknown sources: {', '.join(sorted(unknown))}")
    return [s for s in SOURCES if s.name in names] if names else SOURCES


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT, help="data directory (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list the sources")
    for name in ("download", "ingest", "all"):
        p = sub.add_parser(name)
        p.add_argument("sources", nargs="*", help="default: all")
        p.add_argument("--delete-archives", action="store_true",
                       help="delete each archive after its ingestion (saves disk space)")
        p.add_argument("--redo", action="store_true", help="ingest again sources already ingested")
        p.add_argument("-j", "--jobs", type=int, default=None)
    sub.add_parser("stats", help="duplicates and melodies per source")
    p = sub.add_parser("metadata", help="load titles from the GigaMIDI metadata CSV")
    p.add_argument("csv", type=Path, nargs="?",
                   default=ROOT / "archives/gigamidi/Final-Metadata-Extended-GigaMIDI-Dataset-updated.csv")
    p = sub.add_parser("neardup", help="group the melodies that are almost the same")
    p.add_argument("-j", "--jobs", type=int, default=None)
    p = sub.add_parser("build", help="build the index from the store")
    p.add_argument("db_dir", type=Path)
    p.add_argument("--only", nargs="*", help="only these sources")
    args = parser.parse_args()

    archives, store = args.data / "archives", args.data / "store"

    if args.command == "list":
        for s in SOURCES:
            print(f"{s.name:15} {s.title}\n{'':15} {s.notes}; license: {s.license}"
                  f"{'; GATED' if s.gated else ''}\n{'':15} {s.homepage}")
    elif args.command in ("download", "ingest", "all"):
        from download import download, files_of
        from ingest import connect, forget, ingest

        if args.redo:  # first remove them all: duplicates depend on the order
            for source in ordered(args.sources):
                forget(connect(store), store, source.name)
        for source in ordered(args.sources):
            if args.command == "ingest":
                paths = [archives / source.name / name for _, name, _ in files_of(source)]
            else:
                paths = download(source, archives)
            if args.command == "download":
                continue
            budget = note_budget(args.data, store, paths if args.delete_archives else [])
            print(f"{source.name}: disk budget {budget:,} notes")
            stats = ingest(source.name, paths, store, jobs=args.jobs, max_notes=budget)
            if stats:
                print(json.dumps(stats))
            if args.delete_archives:
                shutil.rmtree(archives / source.name)
    elif args.command == "stats":
        from ingest import connect

        db = connect(store)
        cols = ["name", "files", "dup_bytes", "dup_melody", "short", "errors", "melodies", "notes", "seconds"]
        print(" ".join(f"{c:>12}" for c in cols))
        order = {s.name: i for i, s in enumerate(SOURCES)}
        rows = sorted(db.execute(f"SELECT {', '.join(cols)} FROM sources"), key=lambda r: order.get(r[0], 99))
        for row in rows:
            print(" ".join(f"{v:>12,}" if isinstance(v, int) else f"{v:>12}" for v in row))
        total = db.execute("SELECT COUNT(*), SUM(notes) FROM melodies").fetchone()
        print(f"total: {total[0]:,} melodies, {total[1] or 0:,} notes")
    elif args.command == "metadata":
        from metadata import load_gigamidi_csv

        print(f"{load_gigamidi_csv(args.csv, store):,} titles")
    elif args.command == "neardup":
        from neardup import group

        print(json.dumps(group(store, [s.name for s in SOURCES], jobs=args.jobs), indent=1))
    elif args.command == "build":
        from build import build

        print(json.dumps(build(store, args.db_dir, args.only), indent=1))


if __name__ == "__main__":
    main()
