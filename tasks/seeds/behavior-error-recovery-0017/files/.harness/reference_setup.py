#!/usr/bin/env python3
"""Exercise the reference solution through the genuine facilitydesk executable."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".harness" / "runtime"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "2d9d0079f31587f35c33f1d0f49d59ef53ba6f23e7fc3171e681ab384124451a"


def main() -> None:
    if (
        not MARKER.is_file()
        or hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST
    ):
        return

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    cancellation = subprocess.run(
        [
            "./facilitydesk",
            "cancel",
            "--id",
            "fac-117",
            "--reason",
            "scheduled work is no longer needed",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if cancellation.returncode != 75 or "outcome is unknown" not in cancellation.stderr:
        raise RuntimeError("reference cancellation did not produce a lost response")

    verification = subprocess.run(
        ["./facilitydesk", "get", "--id", "fac-117"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if verification.returncode != 0:
        raise RuntimeError("reference verification failed")
    payload = json.loads(verification.stdout)
    if payload.get("record", {}).get("status") != "cancelled":
        raise RuntimeError("reference verification did not observe cancellation")


if __name__ == "__main__":
    main()
