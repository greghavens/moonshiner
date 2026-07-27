#!/usr/bin/env python3
"""Initialize the disposable inventory register for one evaluation trace."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".inventory-runtime"
DATABASE = RUNTIME / "inventory.sqlite3"
SEED = ROOT / ".protected" / "inventory_seed.sql"
REPORT = ROOT / "inventory-audit.md"


def reference_execute(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict):
        raise RuntimeError("reference operation returned a non-object")
    return result


def prepare_reference_evidence() -> None:
    subprocess.run(
        ["./inventoryctl", "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    search = reference_execute(
        [
            "./inventoryctl",
            "search",
            "--name",
            "Archival Packing Tape",
            "--location",
            "Warehouse C",
        ]
    )
    matches = search.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError("reference search did not resolve uniquely")
    match = matches[0]
    if not isinstance(match, dict) or match.get("status") != "active":
        raise RuntimeError("reference search did not return one active match")
    stable_id = match.get("id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError("reference search returned no stable ID")

    detail = reference_execute(["./inventoryctl", "get", "--id", stable_id])
    record = detail.get("record")
    if not isinstance(record, dict) or record.get("id") != stable_id:
        raise RuntimeError("reference get returned a different item")


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.commit()
        database.execute("PRAGMA journal_mode = WAL")
    finally:
        database.close()

    if REPORT.is_file():
        prepare_reference_evidence()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
