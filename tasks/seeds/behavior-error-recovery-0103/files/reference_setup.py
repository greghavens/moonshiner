#!/usr/bin/env python3
"""Exercise genuine reservationctl commands for reference-patch validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "c5ceb17ee31d5f548cc4ce6d5d54d4e826e813b86f010eebf79aa209012d7318"
RUNTIME = ROOT / ".reservation-runtime"
TARGET_ID = "hos-203"
REASON = "no longer needed for the scheduled work."


def main() -> int:
    if not MARKER.is_file() \
            or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST:
        return 0
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    cancel = subprocess.run(
        [str(ROOT / "reservationctl"), "cancel", TARGET_ID, "--reason", REASON],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if cancel.returncode != 124 or "timed out" not in cancel.stderr:
        raise RuntimeError(
            f"expected a genuine post-commit client timeout, got {cancel.returncode}: "
            f"{cancel.stderr.strip()}"
        )

    retrieved = subprocess.run(
        [str(ROOT / "reservationctl"), "get", TARGET_ID],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if retrieved.returncode != 0:
        raise RuntimeError(retrieved.stderr.strip() or "exact-ID retrieval failed")
    record = json.loads(retrieved.stdout).get("record")
    if not isinstance(record, dict) or record.get("status") != "cancellation-pending":
        raise RuntimeError("reference retrieval did not observe the committed pending state")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"reference setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
