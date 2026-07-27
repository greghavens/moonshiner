#!/usr/bin/env python3
"""Exercise the reference path through the genuine inventory executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".inventory" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "1b1bbf1e073f79e1fc1154644805cf0b759cc0039aafcfae1de65d20a035d2ef"
TARGET_ID = "inv-167"
REASON = "no longer needed for the scheduled work"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "inventory.sqlite3",
        REGISTRY_RUNTIME / "inventory.sqlite3-shm",
        REGISTRY_RUNTIME / "inventory.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "inventory-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    cancellation = subprocess.run(
        [
            "./inventory-registry",
            "cancel",
            "--id",
            TARGET_ID,
            "--reason",
            REASON,
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not exercise uncertain commit")

    retrieval = subprocess.run(
        ["./inventory-registry", "get", "--id", TARGET_ID],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if retrieval.returncode != 0:
        raise RuntimeError(f"reference retrieval failed: {retrieval.stderr.strip()}")
    record = json.loads(retrieval.stdout).get("record")
    if not isinstance(record, dict) or record.get("status") != "cancelled":
        raise RuntimeError("reference retrieval did not resolve the uncertain commit")


if __name__ == "__main__":
    main()
