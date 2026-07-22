#!/usr/bin/env python3
"""Import the seed corpus from the configured source repositories.

The moonshiner seed corpus is tracked in-tree under ``tasks/seeds``. Rather than
author seeds here, we take the latest vetted corpus from a canonical source and,
where the canonical seed is missing or broken, from a fallback source.

  * ``config.source.seed_repository`` — an optional canonical external source.
    Without one, the bundled corpus is canonical.
  * ``config.source.fallback_repository`` — an optional fallback source. Used
    only for a seed the canonical source lacks or left
    incomplete (an authoring agent that died mid-write, a safeguard-rejected
    stub). This encodes the rule "canonical unless it is off, then fall back".

A seed is complete when its ``task.json`` parses and carries every required
field, its ``id`` matches the directory name, ``files/`` exists, and every
protected ``test_files`` entry is present. A seed that is incomplete in BOTH
sources is reported invalid and never half-copied. Seeds already present here
are left untouched unless ``--force``.

Copies are atomic (stage into a sibling temp dir, then swap) and skip installed
``node_modules``/cache trees so a dependency install never bloats the corpus.

Model-free and idempotent — safe to re-run.
  python3 src/import_seeds.py            # canonical + fallback per config
  python3 src/import_seeds.py --dry-run  # report provenance without copying
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from common import CONFIG, ROOT, SEEDS_DIR

REQUIRED = ("id", "lang", "category", "prompt", "verify_cmd", "test_files")


def seeds_dir(repo: str) -> Path:
    """Resolve a source repo's ``tasks/seeds`` directory (relative to root)."""
    base = Path(repo).expanduser()
    if not base.is_absolute():
        base = (ROOT / base).resolve()
    return base / "tasks" / "seeds"


def source_seeds_dir(source: str | None = None) -> Path:
    """The canonical source ``tasks/seeds`` directory (config or override)."""
    repo = source or CONFIG.get("source", {}).get("seed_repository")
    return seeds_dir(repo) if repo else ROOT / "tasks" / "seeds"


def seed_complete(directory: Path) -> str | None:
    """Return a reason string if the source seed is NOT a complete unit."""
    if not directory.is_dir():
        return "directory absent"
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


def resolve(name: str, primary: Path, fallback: Path | None) -> tuple[Path, str, str]:
    """Choose the source directory for one seed id.

    Prefer the canonical source when complete; otherwise fall back. Returns
    ``(chosen_dir, provenance, reason)`` where provenance is ``primary`` /
    ``fallback`` / ``invalid`` and reason explains an invalid outcome.
    """
    primary_why = seed_complete(primary / name)
    if primary_why is None:
        return primary / name, "primary", ""
    if fallback is not None:
        fallback_why = seed_complete(fallback / name)
        if fallback_why is None:
            return fallback / name, "fallback", ""
        return primary / name, "invalid", (
            f"canonical: {primary_why}; fallback: {fallback_why}")
    return primary / name, "invalid", f"canonical: {primary_why}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source",
                        help="Override config.source.seed_repository (canonical)")
    parser.add_argument("--fallback",
                        help="Override config.source.fallback_repository")
    parser.add_argument("--only", help="Comma-separated seed ids to import")
    parser.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be imported without copying")
    args = parser.parse_args(argv)
    if args.force:
        parser.error("--force was removed: existing seeds are immutable")

    primary = source_seeds_dir(args.source)
    if not primary.is_dir():
        print(f"canonical seed directory not found: {primary}", file=sys.stderr)
        return 1
    fallback_repo = args.fallback or CONFIG.get("source", {}).get("fallback_repository")
    fallback = seeds_dir(fallback_repo) if fallback_repo else None
    if fallback is not None and not fallback.is_dir():
        print(f"fallback seed directory not found: {fallback}", file=sys.stderr)
        return 1

    only = {value.strip() for value in args.only.split(",")} if args.only else None
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    candidates = {p.name for p in primary.iterdir() if p.is_dir()}
    if fallback is not None:
        candidates |= {p.name for p in fallback.iterdir() if p.is_dir()}

    imported, backfilled, skipped, invalid = [], [], [], []
    for name in sorted(candidates):
        if only and name not in only:
            continue
        chosen, provenance, reason = resolve(name, primary, fallback)
        if provenance == "invalid":
            invalid.append((name, reason))
            continue
        dest = SEEDS_DIR / name
        if dest.exists():
            skipped.append(name)
            continue
        if not args.dry_run:
            copy_seed(chosen, dest)
        imported.append(name)
        if provenance == "fallback":
            backfilled.append(name)

    for name, why in invalid:
        print(f"[invalid ] {name}: {why}")
    if backfilled:
        print(f"[fallback] {len(backfilled)} from fallback: {', '.join(backfilled)}")
    total = len(imported) + len(skipped) + len(invalid)
    verb = "would import" if args.dry_run else "imported"
    print(f"\n{len(imported)} {verb} ({len(backfilled)} via fallback), "
          f"{len(skipped)} skipped (already present), {len(invalid)} invalid "
          f"of {total} candidate seeds\n  canonical: {primary}\n  fallback:  "
          f"{fallback if fallback is not None else '(none)'}")
    return 1 if invalid else 0


if __name__ == "__main__":
    raise SystemExit(main())
