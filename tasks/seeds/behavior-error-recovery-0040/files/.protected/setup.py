#!/usr/bin/env python3
"""Reset fleet runtime state and run an applied reference proof when present."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".fleet-runtime"
DELIVERABLE = ROOT / "fleet-availability.md"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if DELIVERABLE.exists():
        DELIVERABLE.unlink()

    driver = ROOT / ".reference_solution.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
