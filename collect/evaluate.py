"""Searches known tunes and checks that the right piece comes first.

    uv run collect/evaluate.py data/db-all

For each query: the rank of the first result whose name or title matches
the expected pattern (1 is best, "-" when not in the first 10), and how
many of the first 10 results match it.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from whatthemidi.index import open_database  # noqa: E402
from whatthemidi.notes import parse_query  # noqa: E402

TUNES = [  # (name, query, any key, pattern of the right file)
    ("Für Elise", "E5 D#5 E5 D#5 E5 B4 D5 C5 A4", False, r"elise"),
    ("Für Elise + pickup", "E5 D#5 E5 D#5 E5 B4 D5 C5 A4 C4 E4 A4 B4", False, r"elise"),
    ("Italian Concerto 3", "A#5 F5 E5 F5 A#5 F5 A5 F5 G5 F5 E5 F5 G5 F5 A5 F5 A#5 F5 E5 F5 A#5",
     False, r"971|italian"),
    ("Italian Concerto 1", "F5 A4 A#4 C5 D5 C5 F5 A#4", False, r"971|italian"),
    ("Scarlatti K513", "79,74,71,67,71,67,72,67,74,67,76,67", False, r"513"),
    ("Ode to Joy", "E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4", True, r"ode|joy|9th|neunte"),
    ("Twinkle", "C4 C4 G4 G4 A4 A4 G4 F4 F4 E4 E4 D4 D4 C4", True, r"twinkle|little star|ah,? vous"),
    ("Beethoven 5", "G4 G4 G4 D#4 F4 F4 F4 D4", True, r"5th|fifth|sym\w*\W*(no\.?\s*)?5\b|67"),
    ("Jingle Bells", "E4 E4 E4 E4 E4 E4 E4 G4 C4 D4 E4", True, r"jingle"),
    ("Happy Birthday", "G4 G4 A4 G4 C5 B4 G4 G4 A4 G4 D5 C5", True, r"birthday"),
]


def evaluate(db_dir):
    db = open_database(db_dir)
    print(f"{db_dir}: {db.meta['files']:,} files, {db.meta['notes']:,} notes")
    print(f"{'tune':22} {'rank':>4} {'top10':>5} {'files':>7}  first result")
    ranks = []
    for name, query, transpose, pattern in TUNES:
        r = db.search(parse_query(query), transpose=transpose, limit=10, context=0)
        hits = [bool(re.search(pattern, f"{x['file']} {x['title'] or ''}", re.I)) for x in r["results"]]
        rank = hits.index(True) + 1 if any(hits) else None
        ranks.append(rank)
        first = r["results"][0]["file"][:50] if r["results"] else "(no match)"
        print(f"{name:22} {rank or '-':>4} {sum(hits):>5} {r['files_total']:>7,}  {first}")
    found = [x for x in ranks if x]
    print(f"first: {sum(x == 1 for x in ranks)}/{len(TUNES)}, in top 10: {len(found)}/{len(TUNES)}")
    return ranks


if __name__ == "__main__":
    for path in sys.argv[1:]:
        evaluate(Path(path))
