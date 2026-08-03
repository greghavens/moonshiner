"""One deduplicated seed-authoring queue with configurable workers."""
from __future__ import annotations

import argparse
import fcntl
import subprocess
import sys
import shutil
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from common import CONFIG, STORAGE_ROOT, load_seeds, synthetic_tool_contract
from configuration import PROJECT_ROOT, load_config
from runtimes.availability import INFRASTRUCTURE_EXIT, USAGE_LIMIT_EXIT
from seed_inventory import (authored_ids, documented_plan_items, plan_priorities,
                            retired_seed_ids)
from seed_repo import ensure as ensure_seed_repo

CLAIMS = STORAGE_ROOT / "locks" / "seed-authoring"


def _moonshiner() -> str:
    executable = shutil.which("moonshiner")
    if not executable:
        raise FileNotFoundError("the installed moonshiner executable was not found")
    return executable


def author_one(seed_id: str, plans: dict[str, str]) -> tuple[str, int]:
    """Run one seed exactly once, even with competing queue coordinators."""
    CLAIMS.mkdir(parents=True, exist_ok=True)
    with (CLAIMS / f"{seed_id}.lock").open("a+") as claim:
        fcntl.flock(claim, fcntl.LOCK_EX)
        if seed_id in authored_ids():
            return seed_id, 0
        existing = next((seed for seed in load_seeds(only={seed_id})
                         if synthetic_tool_contract(seed)), None)
        command = [_moonshiner(), "seed", "run", "--id", seed_id,
                   "--brief", plans[seed_id], "--yes"]
        if existing:
            command.append("--replace-synthetic")
        return seed_id, subprocess.run(command, cwd=PROJECT_ROOT).returncode


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner seed queue")
    parser.add_argument("--workers", type=int, default=0)
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    # Accepted seeds are promoted into the repository, so it must be on disk
    # before any authoring starts.
    if not args.dry_run:
        ensure_seed_repo()
    workers = args.workers or int((CONFIG.get("pipeline", {}).get("seed") or {}).get("workers", 1))
    if not 1 <= workers <= 64:
        parser.error("--workers must be from 1 through 64")
    plans = documented_plan_items()
    authored = authored_ids()
    retired = retired_seed_ids()
    priorities = plan_priorities()
    missing = sorted(set(plans) - authored - retired,
                     key=lambda seed_id: (-priorities.get(seed_id, 0), seed_id))
    print(f"seed queue: authored={len(authored)}, retired={len(retired)}, "
          f"waiting={len(missing)}, workers={workers}")
    if not missing or args.dry_run:
        return 0
    if not args.yes:
        parser.error("metered seed authoring requires --yes")
    failed = 0
    out_of_quota = False
    pending = list(missing)
    def configured_workers() -> int:
        if args.workers:
            return args.workers
        value = int(((load_config().get("pipeline") or {}).get("seed") or {})
                    .get("workers", workers))
        if not 1 <= value <= 64:
            raise ValueError("pipeline.seed.workers must be from 1 through 64")
        return value
    with ThreadPoolExecutor(max_workers=64, thread_name_prefix="seed-worker") as pool:
        futures: set = set()
        while pending or futures:
            target = configured_workers()
            while pending and len(futures) < target and not out_of_quota:
                futures.add(pool.submit(author_one, pending.pop(0), plans))
            if not futures:
                break
            done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                futures.remove(future)
                seed_id, code = future.result()
                # One seed reporting no quota means every remaining seed would too.
                # A broken environment is not this seed's fault and will break
                # every seed after it. Stop authoring entirely until it is fixed.
                if code == INFRASTRUCTURE_EXIT:
                    out_of_quota = True
                    pending.clear()
                    for queued in futures:
                        queued.cancel()
                    print(f"[seed stopped] {seed_id}: INFRASTRUCTURE FAILURE — "
                          "no further seeds will be authored until it is fixed",
                          file=sys.stderr, flush=True)
                    continue
                if code == USAGE_LIMIT_EXIT:
                    out_of_quota = True
                    pending.clear()
                    for queued in futures:
                        queued.cancel()
                    print(f"[seed stopped] {seed_id}: runtime out of quota",
                          file=sys.stderr, flush=True)
                    continue
                failed += bool(code)
                print(f"[seed {'failed' if code else 'authored'}] {seed_id}", flush=True)
    if out_of_quota:
        return USAGE_LIMIT_EXIT
    return 1 if failed else 0
