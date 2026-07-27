#!/usr/bin/env python3
"""Prepare deterministic reference validation for the appointment task."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".appointments"
MARKER = ROOT / ".reference_solution"
DRIVER = ROOT / "reference_driver.py"
MARKER_DIGEST = "7cc9676576a382d49a7f43fdaea50fe9d293d63fab6db59a7d5be4ef30571400"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    marker_exists = MARKER.is_file() and not MARKER.is_symlink()
    driver_exists = DRIVER.is_file() and not DRIVER.is_symlink()
    if not marker_exists and not driver_exists:
        return 0
    if not marker_exists or not driver_exists:
        raise RuntimeError("reference proof is incomplete")
    if hashlib.sha256(MARKER.read_bytes()).hexdigest() != MARKER_DIGEST:
        raise RuntimeError("reference marker is invalid")

    subprocess.run(
        [sys.executable, "-B", str(DRIVER)],
        cwd=ROOT,
        check=True,
        timeout=15,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
