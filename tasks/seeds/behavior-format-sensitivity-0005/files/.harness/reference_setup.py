#!/usr/bin/env python3
"""Exercise the reference solution through the genuine support executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".support" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "ff39e503ca18da8af1555c63b597e33b8d90cb03cbc015457ce7e19a62bca3d2"
EXPECTED_FIELDS = {
    "description",
    "id",
    "lifecycle",
    "location",
    "name",
    "opened_on",
    "priority",
    "status",
}


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "support.sqlite3",
        REGISTRY_RUNTIME / "support.sqlite3-shm",
        REGISTRY_RUNTIME / "support.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "support-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    reset_generated_state()
    completed = subprocess.run(
        ["./support-case", "get", "--id", "sup-105"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    payload = json.loads(completed.stdout)
    record = payload.get("case")
    if not isinstance(record, dict):
        raise RuntimeError("reference lookup did not return a complete case object")
    if record.get("id") != "sup-105" or set(record) != EXPECTED_FIELDS:
        raise RuntimeError("reference lookup did not return the requested full case")


if __name__ == "__main__":
    main()
