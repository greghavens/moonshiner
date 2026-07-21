"""One deduplicated seed-authoring queue with configurable workers."""
from __future__ import annotations

import argparse
import subprocess
import sys
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import CONFIG, load_seeds, synthetic_tool_contract
from configuration import PROJECT_ROOT
from seed_inventory import authored_ids, documented_plan_items, retired_seed_ids


def _moonshiner() -> str:
    executable = shutil.which("moonshiner")
    if not executable:
        raise FileNotFoundError("the installed moonshiner executable was not found")
    return executable


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
    authored = authored_ids()
    retired = retired_seed_ids()
    missing = sorted(set(plans) - authored - retired)
    print(f"seed queue: authored={len(authored)}, retired={len(retired)}, "
          f"waiting={len(missing)}, workers={workers}")
    if not missing or args.dry_run:
        return 0
    if not args.yes:
        parser.error("metered seed authoring requires --yes")
    def author(seed_id: str) -> tuple[str, int]:
        if seed_id in authored_ids():
            return seed_id, 0
        existing = next((seed for seed in load_seeds(only={seed_id})
                         if synthetic_tool_contract(seed)), None)
        command = [_moonshiner(), "seed", "run", "--id", seed_id,
                   "--brief", plans[seed_id], "--yes"]
        if existing:
            command.append("--replace-synthetic")
        return seed_id, subprocess.run(command, cwd=PROJECT_ROOT).returncode

    failed = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="seed-worker") as pool:
        futures = {pool.submit(author, seed_id) for seed_id in missing}
        for future in as_completed(futures):
            seed_id, code = future.result()
            failed += bool(code)
            print(f"[seed {'failed' if code else 'authored'}] {seed_id}", flush=True)
    return 1 if failed else 0
