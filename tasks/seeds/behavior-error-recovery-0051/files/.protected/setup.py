#!/usr/bin/env python3
"""Prepare deterministic reference validation for the recruiting task."""

from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".recruiting"
MARKER = ROOT / ".reference_solution"
DRIVER = ROOT / "reference_driver.py"
MARKER_DIGEST = "b7cfabab61eff24a7b8e4787e75d66d53de3a0826d7213081d173a538d144556"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    if not MARKER.is_file() or not DRIVER.is_file():
        return 0
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
