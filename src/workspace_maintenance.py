"""Remove materialized workspaces for currently accepted trace tasks."""
from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from common import WORKSPACES
from run_state import connect
from seed_inventory import accepted_ids


def _belongs_to(seed_id: str, name: str) -> bool:
    prefix = r"(?:environment-|screen-|review-|validate-)?"
    suffix = r"(?:-provisioned)?(?:-[0-9a-f]{10})?"
    return re.fullmatch(prefix + re.escape(seed_id) + suffix, name) is not None


def prune(workspaces: Path = WORKSPACES) -> tuple[int, int]:
    db = connect()
    try:
        accepted = accepted_ids(db)
    finally:
        db.close()
    removed = 0
    reclaimed = 0
    if not workspaces.is_dir():
        return removed, reclaimed
    for path in workspaces.iterdir():
        if not path.is_dir() or not any(_belongs_to(seed_id, path.name)
                                        for seed_id in accepted):
            continue
        if path.resolve().parent != workspaces.resolve():
            raise ValueError(f"unsafe workspace path: {path}")
        reclaimed += sum(item.stat().st_size for item in path.rglob("*")
                         if item.is_file())
        shutil.rmtree(path)
        removed += 1
    return removed, reclaimed


def prune_old(workspaces: Path = WORKSPACES) -> tuple[int, int]:
    """Remove every abandoned workspace while preserving live leased jobs."""
    db = connect()
    try:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        active = {str(row[0]) for row in db.execute(
            "SELECT seed_id FROM jobs WHERE status='infrastructure_blocked' OR "
            "(status='running' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at>?)", (now,))}
    finally:
        db.close()
    removed = 0
    reclaimed = 0
    if not workspaces.is_dir():
        return removed, reclaimed
    for path in workspaces.iterdir():
        if not path.is_dir() or any(_belongs_to(seed_id, path.name)
                                    for seed_id in active):
            continue
        if path.resolve().parent != workspaces.resolve():
            raise ValueError(f"unsafe workspace path: {path}")
        reclaimed += sum(item.stat().st_size for item in path.rglob("*")
                         if item.is_file())
        shutil.rmtree(path)
        removed += 1
    return removed, reclaimed


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    if argv not in ([], ["--all-old"]):
        raise SystemExit("usage: moonshiner maintenance "
                         "prune-accepted-workspaces [--all-old]")
    removed, reclaimed = prune_old() if argv else prune()
    label = "old" if argv else "accepted"
    print(f"removed {removed} {label} workspaces; "
          f"reclaimed {reclaimed} bytes")
    return 0
