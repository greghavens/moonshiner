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


def active_claim_ids(claims: Path = CLAIMS) -> set[str]:
    """Return seed IDs whose queue claims are currently held by a worker."""
    active: set[str] = set()
    if not claims.is_dir():
        return active
    for path in claims.glob("*.lock"):
        try:
            claim = path.open("r")
        except OSError:
            continue
        try:
            # Status readers take shared locks so concurrent status commands do
            # not mistake one another for author workers. Workers hold exclusive
            # locks for the full subprocess lifetime.
            fcntl.flock(claim, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            active.add(path.stem)
        else:
            fcntl.flock(claim, fcntl.LOCK_UN)
        finally:
            claim.close()
    return active


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
    stop_exit = None
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
        futures: dict = {}
        while pending or futures:
            target = configured_workers()
            while pending and len(futures) < target and stop_exit is None:
                seed_id = pending.pop(0)
                futures[pool.submit(author_one, seed_id, plans)] = seed_id
            if not futures:
                break
            done, _ = wait(futures, timeout=2, return_when=FIRST_COMPLETED)
            for future in done:
                seed_id = futures.pop(future)
                try:
                    _, code = future.result()
                    detail = f"worker exited {code}"
                except Exception as error:
                    code = INFRASTRUCTURE_EXIT
                    detail = f"{type(error).__name__}: {error}"
                if code:
                    # This is the provider-independent, fail-closed boundary.
                    # Only success may claim another seed: an adapter cannot
                    # turn a new or unrecognised provider outage into a sweep.
                    failure_exit = (USAGE_LIMIT_EXIT if code == USAGE_LIMIT_EXIT
                                    else INFRASTRUCTURE_EXIT)
                    if stop_exit is None or failure_exit == INFRASTRUCTURE_EXIT:
                        stop_exit = failure_exit
                    pending.clear()
                    for queued in futures:
                        queued.cancel()
                    if failure_exit == USAGE_LIMIT_EXIT:
                        print(f"[seed stopped] {seed_id}: runtime out of quota",
                              file=sys.stderr, flush=True)
                    else:
                        print(f"[seed stopped] {seed_id}: INFRASTRUCTURE FAILURE — "
                              f"{detail}; no further seeds will be authored until "
                              "it is fixed", file=sys.stderr, flush=True)
                    continue
                print(f"[seed authored] {seed_id}", flush=True)
    return stop_exit or 0
