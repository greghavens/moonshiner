#!/usr/bin/env python3
"""Exercise the reference workflow through the genuine expense executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
STORE_RUNTIME = ROOT / ".expense-store" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "173cae5e66b847eefac4e91d2939549018d74301365a2c5adb1a72fe1cf536c8"


def reset_generated_state() -> None:
    for path in (
        STORE_RUNTIME / "ledger.sqlite3",
        STORE_RUNTIME / "ledger.sqlite3-shm",
        STORE_RUNTIME / "ledger.sqlite3-wal",
        STORE_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "expense-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_client(arguments: list[str]) -> dict:
    result = subprocess.run(
        ["./expensectl", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "reference expense operation failed: " + result.stderr.strip()
        )
    return json.loads(result.stdout)


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    search = run_client(
        [
            "search",
            "--name",
            "Intercity Rail Fare",
            "--location",
            "Chicago",
        ]
    )
    matches = search.get("matches")
    if not isinstance(matches, list) or len(matches) != 1:
        raise RuntimeError("reference search did not resolve uniquely")
    stable_id = matches[0].get("stable_id")
    if not isinstance(stable_id, str) or not stable_id:
        raise RuntimeError("reference search returned no stable ID")

    retrieved = run_client(["get", "--id", stable_id])
    record = retrieved.get("record")
    if not isinstance(record, dict):
        raise RuntimeError("reference get returned no complete record")
    if (
        record.get("stable_id") != stable_id
        or record.get("name") != "Intercity Rail Fare"
        or record.get("location") != "Chicago"
        or record.get("status") != "active"
    ):
        raise RuntimeError("reference get returned the wrong complete record")


if __name__ == "__main__":
    main()
