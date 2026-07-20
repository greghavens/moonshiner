"""One deduplicated seed-authoring queue with configurable workers."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import CONFIG
from configuration import PROJECT_ROOT
from seed_inventory import authored_ids, documented_plan_items


def _moonshiner() -> str:
    executable = Path(sys.executable).resolve().parent / "moonshiner"
    return str(executable)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner seed queue")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    workers = args.workers or int((CONFIG.get("pipeline", {}).get("seed") or {}).get("workers", 1))
    if not 1 <= workers <= 64:
        parser.error("--workers must be from 1 through 64")
    plans = documented_plan_items()
    missing = sorted(set(plans) - authored_ids())
    print(f"seed queue: authored={len(authored_ids())}, waiting={len(missing)}, workers={workers}")
    if not missing or args.dry_run:
        return 0
    if not args.yes:
        parser.error("metered seed authoring requires --yes")
    def author(seed_id: str) -> tuple[str, int]:
        if seed_id in authored_ids():
            return seed_id, 0
        command = [_moonshiner(), "seed", "run", "--id", seed_id,
                   "--brief", plans[seed_id], "--yes"]
        return seed_id, subprocess.run(command, cwd=PROJECT_ROOT).returncode

    failed = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="seed-worker") as pool:
        futures = {pool.submit(author, seed_id) for seed_id in missing}
        for future in as_completed(futures):
            seed_id, code = future.result()
            failed += bool(code)
            print(f"[seed {'failed' if code else 'authored'}] {seed_id}", flush=True)
    return 1 if failed else 0
