#!/usr/bin/env python3
"""Resumable, safely parallel seed authoring for Waves 10, 11, 14, 17, and 18."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from author_explicit_waves import catalog_items
from author_matrix_waves import matrix_items
from common import CONFIG, ROOT, SEEDS_DIR, STORAGE_ROOT


def _claim(seed_id: str) -> Path | None:
    """Atomically claim a seed so concurrent authors never buy duplicate work."""
    claims = STORAGE_ROOT / "runs" / "seed-author-claims"
    claims.mkdir(parents=True, exist_ok=True)
    claim = claims / seed_id
    try:
        claim.mkdir()
    except FileExistsError:
        return None
    (claim / "owner").write_text(f"pid={os.getpid()} token={uuid.uuid4().hex}\n")
    return claim


def _author(item: tuple[int, int, str, str], total: int) -> tuple[str, int]:
    position, wave, seed_id, brief = item
    if (SEEDS_DIR / seed_id).exists():
        return f"[{position}/{total}] existing {seed_id}", 0
    claim = _claim(seed_id)
    if claim is None:
        return f"[{position}/{total}] claimed by another author {seed_id}", 0
    try:
        if (SEEDS_DIR / seed_id).exists():
            return f"[{position}/{total}] existing {seed_id}", 0
        print(f"[{position}/{total}] Wave {wave} author {seed_id}", flush=True)
        result = subprocess.run([
            sys.executable, str(ROOT / "moonshiner.py"), "seed", "run", "--id",
            seed_id, "--brief", brief, "--max-attempts", "3", "--yes",
        ], cwd=ROOT)
        return f"[{position}/{total}] finished {seed_id} rc={result.returncode}", result.returncode
    finally:
        try:
            (claim / "owner").unlink(missing_ok=True)
            claim.rmdir()
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int,
                        default=int(CONFIG.get("pipeline", {}).get("seed", {}).get("workers", 2)))
    parser.add_argument("--reverse", action="store_true",
                        help="Consume the plan from the opposite end (safe migration helper).")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if not args.yes:
        parser.error("metered wave authoring requires --yes")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    planned = []
    for wave in (10, 11, 14):
        for seed_id, chunk, text in catalog_items(
                args.catalog_dir / f"WAVE{wave}_USECASES.md"):
            brief = (f"Wave {wave}, chunk {chunk}. Author this exact adopted use case; "
                     f"do not broaden or omit its requirements. Include catalog provenance "
                     f"in task.json as {{\"wave\": {wave}, \"chunk\": \"{chunk}\"}}.\n\n{text}")
            planned.append((wave, seed_id, brief))
    for wave in (17, 18):
        domain = "site reliability engineering" if wave == 17 else "firmware engineering"
        for seed_id, chunk, objective in matrix_items(
                args.catalog_dir / f"WAVE{wave}_USECASES.md", wave):
            brief = f"""Wave {wave}, chunk {chunk}, supervised {domain} curriculum.
Author this exact objective as a deterministic, workspace-local coding-repair seed:
{objective}

Use only simulated services/devices and fake non-secret fixtures. No public network,
live infrastructure, real credentials, host-global mutation, correctness sleeps, or
destructive host actions. Establish scope, authority, evidence, reversible action
boundaries, and observable success. Protected tests must prove every requirement,
adjacent invariants, failure behavior, and cleanup or rollback. Include catalog
provenance in task.json as {{"wave": {wave}, "chunk": "{chunk}"}}. Do not broaden it."""
            planned.append((wave, seed_id, brief))

    indexed = [(position, wave, seed_id, brief)
               for position, (wave, seed_id, brief) in enumerate(planned, 1)]
    if args.reverse:
        indexed.reverse()
    print(f"unified Wave 10+ plan: {len(planned)} seeds; authors={args.workers}", flush=True)
    failed = False
    with ThreadPoolExecutor(max_workers=args.workers,
                            thread_name_prefix="seed-author") as pool:
        futures = [pool.submit(_author, item, len(planned)) for item in indexed]
        for future in as_completed(futures):
            message, returncode = future.result()
            print(message, flush=True)
            failed = failed or returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
