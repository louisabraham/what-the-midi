"""Titles and artists from the GigaMIDI metadata, by MD5 of the file.

GigaMIDI names its files by MD5 and gives a title and an artist for some
of them in a CSV file. Lakh full also names its files by MD5, and the
other collections share many files with GigaMIDI: the MD5 finds them.
"""

import csv
import sys

from ingest import connect

csv.field_size_limit(sys.maxsize)

def load_gigamidi_csv(path, store):
    db = connect(store)  # creates the metadata table
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        header = next(reader)
        i_md5, i_title, i_artist = header.index("md5"), header.index("title"), header.index("artist")
        for row in reader:
            if len(row) <= max(i_md5, i_title, i_artist):
                continue
            title, artist = row[i_title].strip(), row[i_artist].strip()
            if title:
                rows.append((row[i_md5].strip().lower(), title, artist or None))
    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?, ?, ?)", rows)
    db.commit()
    return len(rows)


def display_title(title, artist):
    return f"{artist} - {title}" if artist else title
