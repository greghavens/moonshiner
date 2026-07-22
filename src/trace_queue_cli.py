"""Explicit generic trace-queue control."""
from __future__ import annotations

import argparse
from pathlib import Path

from common import load_seeds
from run_state import connect, enqueue_traces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner trace queue add")
    parser.add_argument("action", choices=["add"])
    parser.add_argument("--ids-file", required=True)
    parser.add_argument("--front", action="store_true")
    parser.add_argument("--fresh-attempts", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("queue changes require --yes")
    ids = [line.strip() for line in Path(args.ids_file).read_text().splitlines()
           if line.strip()]
    known = {seed["id"] for seed in load_seeds()}
    unknown = sorted(set(ids) - known)
    if unknown:
        parser.error(f"{len(unknown)} IDs are not catalogued; first: {unknown[0]}")
    db = connect()
    try:
        count = enqueue_traces(db, ids, front=args.front,
                               fresh_attempts=args.fresh_attempts)
    finally:
        db.close()
    location = "front" if args.front else "tail"
    print(f"queued {count} traces at the {location}")
    return 0
