#!/usr/bin/env python3
"""Reset generated state and run an applied reference driver when present."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".course-runtime"
DELIVERABLE = ROOT / "course-availability.md"
REFERENCE_DRIVER = ROOT / ".reference_solution.py"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if DELIVERABLE.exists():
        DELIVERABLE.unlink()
    if REFERENCE_DRIVER.is_file():
        subprocess.run(
            [sys.executable, "-B", str(REFERENCE_DRIVER)],
            cwd=ROOT,
            check=True,
            timeout=15,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
