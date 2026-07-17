#!/usr/bin/env python3
"""Print the shelf manifest for the volunteer board.

Usage: python3 export_shelf.py <intake.csv>
CSV rows are "category,qty_kg"; lines starting with '#' are skipped.
"""
import csv
import sys

from ..util import format_qty
from pantry.storage import BinIndex

SHELF = [("A1", 20.0), ("A2", 20.0), ("B1", 35.0)]


def main(argv):
    if len(argv) != 2:
        print("usage: export_shelf.py <intake.csv>", file=sys.stderr)
        return 2
    index = BinIndex(SHELF)
    with open(argv[1], newline="") as fh:
        for row in csv.reader(fh):
            if not row or row[0].startswith("#"):
                continue
            index.place(row[0].strip(), float(row[1]))
    for bin_id, load, cats in index.manifest():
        label = ", ".join(cats) if cats else "empty"
        print(f"{bin_id}: {format_qty(load, 'kg')} — {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
