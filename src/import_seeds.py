#!/usr/bin/env python3
"""Import the seed corpus from the configured source repository.

The moonshiner seed corpus is tracked in-tree under ``tasks/seeds``. Rather than
author seeds here, we take the latest vetted corpus from the source repository
named by ``config.source.seed_repository`` (default ``../sol-code``) and copy
each COMPLETE seed directory in. A seed is complete when its ``task.json``
parses and carries every required field, its ``id`` matches the directory name,
``files/`` exists, and every protected ``test_files`` entry is present; a partial
seed (an authoring agent died mid-write) is skipped, never half-copied. Seeds
that already exist here are left untouched unless ``--force``.

Copies are atomic (stage into a sibling temp dir, then swap) and skip installed
``node_modules``/cache trees so a dependency install never bloats the corpus.

Model-free and idempotent — safe to re-run.
  python3 src/import_seeds.py            # import from config.source
  python3 src/import_seeds.py --dry-run  # report what would change
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from common import CONFIG, ROOT, SEEDS_DIR

REQUIRED = ("id", "lang", "category", "prompt", "verify_cmd", "test_files")


def source_seeds_dir(source: str | None = None) -> Path:
    """Resolve the source ``tasks/seeds`` directory (relative to repo root)."""
    repo = source or CONFIG.get("source", {}).get("seed_repository", "../sol-code")
    base = Path(repo).expanduser()
    if not base.is_absolute():
        base = (ROOT / base).resolve()
    return base / "tasks" / "seeds"


def seed_complete(directory: Path) -> str | None:
    """Return a reason string if the source seed is NOT a complete unit."""
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
    return None


def copy_seed(source_dir: Path, dest_dir: Path) -> None:
    """Copy one seed atomically: stage into a sibling temp dir, then swap in."""
    staging = dest_dir.with_name(dest_dir.name + ".importing")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(
        source_dir, staging,
        ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc"))
    shutil.rmtree(dest_dir, ignore_errors=True)
    staging.replace(dest_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source",
                        help="Override config.source.seed_repository")
    parser.add_argument("--only", help="Comma-separated seed ids to import")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite seeds that already exist here")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported without copying")
    args = parser.parse_args(argv)

    source = source_seeds_dir(args.source)
    if not source.is_dir():
        print(f"source seed directory not found: {source}", file=sys.stderr)
        return 1
    only = {value.strip() for value in args.only.split(",")} if args.only else None
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    imported, skipped, invalid = [], [], []
    for source_dir in sorted(p for p in source.iterdir() if p.is_dir()):
        name = source_dir.name
        if only and name not in only:
            continue
        why = seed_complete(source_dir)
        if why:
            invalid.append((name, why))
            continue
        dest = SEEDS_DIR / name
        if dest.exists() and not args.force:
            skipped.append(name)
            continue
        if not args.dry_run:
            copy_seed(source_dir, dest)
        imported.append(name)

    for name, why in invalid:
        print(f"[invalid ] {name}: {why}")
    total = len(imported) + len(skipped) + len(invalid)
    verb = "would import" if args.dry_run else "imported"
    print(f"\n{len(imported)} {verb}, {len(skipped)} skipped (already present), "
          f"{len(invalid)} invalid of {total} source seeds in {source}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
