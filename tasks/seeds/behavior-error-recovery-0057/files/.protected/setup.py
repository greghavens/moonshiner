#!/usr/bin/env python3
"""Prepare deterministic reference validation for the facilities task."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".facilities"
MARKER = ROOT / ".reference_solution"
DRIVER = ROOT / "reference_driver.py"
MARKER_DIGEST = "35ab9cbc93affa2b6e16ec448ed111dbb6977ec0288fcc5d91536a29d6aca65a"


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
