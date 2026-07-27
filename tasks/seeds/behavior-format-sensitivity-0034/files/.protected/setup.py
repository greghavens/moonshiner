#!/usr/bin/env python3
"""Prepare the expense sandbox for tracing or reference validation."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DRIVER = ROOT / "reference_driver.py"


def main() -> int:
    # A normal trace starts from the authored baseline, where the reference
    # driver is absent. Model-free validation applies the reference patch
    # first, so only that path executes the driver.
    if REFERENCE_DRIVER.is_file():
        subprocess.run(
            [sys.executable, "-B", str(REFERENCE_DRIVER)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
