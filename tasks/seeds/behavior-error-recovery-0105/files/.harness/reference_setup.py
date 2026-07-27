#!/usr/bin/env python3
"""Exercise the reference behavior through the genuine case executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_RUNTIME = ROOT / ".support" / "runtime"
HARNESS_RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "ba15f410005497fdcaf97f5953a55496a2b94f9ea70bc2a1394342ebf1af68d8"
TARGET_ID = "sup-205"
REASON = "no longer needed for the scheduled work"


def reset_generated_state() -> None:
    for path in (
        REGISTRY_RUNTIME / "support.sqlite3",
        REGISTRY_RUNTIME / "support.sqlite3-shm",
        REGISTRY_RUNTIME / "support.sqlite3-wal",
        REGISTRY_RUNTIME / "initialize.lock",
        HARNESS_RUNTIME / "case-audit.jsonl",
    ):
        path.unlink(missing_ok=True)


def run_registry(arguments: list[str], expected_code: int) -> dict:
    process = subprocess.run(
        ["./case-registry", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if process.returncode != expected_code:
        raise RuntimeError(
            "reference registry operation returned an unexpected exit status: "
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
    timeout = run_registry(
        ["cancel", "--id", TARGET_ID, "--reason", REASON], expected_code=75
    )
    if "outcome unknown" not in timeout.get("error", ""):
        raise RuntimeError("reference cancellation did not produce an uncertain outcome")
    retrieved = run_registry(["get", "--id", TARGET_ID], expected_code=0)
    record = retrieved.get("record", {})
    if record.get("status") != "cancelled":
        raise RuntimeError("reference exact-ID retrieval did not resolve cancellation")


if __name__ == "__main__":
    main()
