#!/usr/bin/env python3
"""Integrity audit for the tracked seed corpus.

A seed is COMPLETE when its ``task.json`` parses and carries every required
field, its ``id`` matches the directory name, ``files/`` exists and contains
every protected ``test_files`` entry, and a non-empty ``reference_fix.patch``
proves local solvability. Holdout tasks are patch-exempt: they are vetted by
held-out evaluation, not by a shipped reference fix.

A partial seed (an authoring agent that died mid-write) poisons trace batches
and blocks re-import, so this prints one line per seed and exits non-zero if any
are partial. Deletion is a human decision — this only reports.

Model-free.
  python3 src/audit_seeds.py
  python3 src/audit_seeds.py --ids   # also emit complete ids / partial dirs
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from common import CONFIG, SEEDS_DIR

REQUIRED = ("id", "lang", "category", "prompt", "verify_cmd", "test_files")
# Pre-spec pilot seeds predate the reference-patch requirement; their
# solvability is proven by actual passing teacher traces, not a shipped fix.
PILOT_EXEMPT = {"py-lru-eviction", "py-config-merge", "go-worker-pool",
                "ts-pagination"}
PATCH_EXEMPT = PILOT_EXEMPT | set(CONFIG.get("holdout_tasks", []))


def check(directory: Path) -> str | None:
    """Return a reason string if the seed is partial, else None."""
    task_path = directory / "task.json"
    if not task_path.exists():
        return "no task.json"
    try:
        task = json.loads(task_path.read_text())
    except json.JSONDecodeError as error:
        return f"task.json invalid: {error}"
    missing = [key for key in REQUIRED if not task.get(key)]
    if missing:
        return f"task.json missing {missing}"
    if task["id"] != directory.name:
        return f"id {task['id']!r} != dir name"
    files = directory / "files"
    if not files.is_dir():
        return "no files/"
    absent = [name for name in task["test_files"] if not (files / name).exists()]
    if absent:
        return f"test files absent: {absent}"
    patch = directory / "reference_fix.patch"
    if directory.name not in PATCH_EXEMPT and (
            not patch.exists() or patch.stat().st_size == 0):
        return "reference_fix.patch missing/empty"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", action="store_true",
                        help="Also print complete ids and partial dirs")
    args = parser.parse_args(argv)

    if not SEEDS_DIR.is_dir():
        print(f"no seed corpus at {SEEDS_DIR} — run src/import_seeds.py first",
              file=sys.stderr)
        return 1

    complete, partial = [], []
    for directory in sorted(p for p in SEEDS_DIR.iterdir() if p.is_dir()):
        why = check(directory)
        (partial if why else complete).append((directory.name, why))
    for name, _ in complete:
        print(f"[complete] {name}")
    for name, why in partial:
        print(f"[PARTIAL ] {name}: {why}")
    print(f"\n{len(complete)} complete, {len(partial)} partial")
    if args.ids:
        print("complete-ids:", ",".join(name for name, _ in complete))
        print("partial-dirs:",
              " ".join(str(SEEDS_DIR / name) for name, _ in partial))
    return 1 if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
