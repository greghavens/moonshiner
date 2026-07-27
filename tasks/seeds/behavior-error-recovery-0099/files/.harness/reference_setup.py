#!/usr/bin/env python3
"""Exercise the reference recovery flow through the genuine executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "4e23e14c67692517e795fa8171b141267fba05c9b4d74f612267698d7a506de1"
TARGET_ID = "pub-199"
REASON = "no longer needed for the scheduled work."


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, capture_output=True, text=True)


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    shutil.rmtree(RUNTIME, ignore_errors=True)

    cancellation = run(
        ["./rebate-desk", "cancel", "--id", TARGET_ID, "--reason", REASON]
    )
    if cancellation.returncode != 124:
        raise RuntimeError("reference cancellation did not produce the uncertain outcome")

    retrieval = run(["./rebate-desk", "get", "--id", TARGET_ID])
    if retrieval.returncode != 0:
        raise RuntimeError("reference exact-ID retrieval failed")
    record = json.loads(retrieval.stdout).get("application", {})
    if record.get("id") != TARGET_ID or record.get("status") != "cancelled":
        raise RuntimeError("reference retrieval did not resolve the committed cancellation")

    # The returned status is not exactly active, so the reference flow must stop
    # without issuing a second cancellation.


if __name__ == "__main__":
    main()
