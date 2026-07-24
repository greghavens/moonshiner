"""Remove materialized workspaces for currently accepted trace tasks."""
from __future__ import annotations

import re
import shutil
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


def main(argv: list[str] | None = None) -> int:
    if argv:
        raise SystemExit("moonshiner maintenance prune-accepted-workspaces "
                         "takes no arguments")
    removed, reclaimed = prune()
    print(f"removed {removed} accepted workspaces; "
          f"reclaimed {reclaimed} bytes")
    return 0
