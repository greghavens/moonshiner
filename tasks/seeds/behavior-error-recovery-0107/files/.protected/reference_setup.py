#!/usr/bin/env python3
"""Exercise the reference recovery path through the genuine executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "532a3876582000e07fa2fbd3adb6fd699ea8def93c10e178ac9515902bfe4dd3"
TARGET_ID = "inv-207"
REASON = "no longer needed for the scheduled work"


def reset_generated_state() -> None:
    for path in (
        ROOT / ".inventory" / "runtime" / "inventory.sqlite3",
        ROOT / ".inventory" / "runtime" / "inventory.sqlite3-shm",
        ROOT / ".inventory" / "runtime" / "inventory.sqlite3-wal",
        ROOT / ".inventory" / "runtime" / "initialize.lock",
        ROOT / ".protected" / "runtime" / "inventory-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_inventory(arguments: list[str], expected_code: int) -> dict:
    process = subprocess.run(
        ["./inventoryctl", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if process.returncode != expected_code:
        raise RuntimeError(
            "reference inventory operation returned an unexpected exit status: "
            + process.stderr.strip()
        )
    stream = process.stderr if expected_code else process.stdout
    return json.loads(stream)


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    timeout = run_inventory(
        ["cancel", "--id", TARGET_ID, "--reason", REASON], expected_code=75
    )
    if "outcome unknown" not in timeout.get("error", ""):
        raise RuntimeError("reference cancellation did not produce uncertainty")

    retrieved = run_inventory(["get", "--id", TARGET_ID], expected_code=0)
    record = retrieved.get("record", {})
    if record.get("status") != "cancellation-pending":
        raise RuntimeError("reference retrieval did not resolve the committed request")


if __name__ == "__main__":
    main()
