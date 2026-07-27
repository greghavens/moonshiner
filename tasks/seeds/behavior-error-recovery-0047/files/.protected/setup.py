#!/usr/bin/env python3
"""Reset generated inventory state and run an applied reference proof."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".inventory-runtime"
REPORT = ROOT / "cancellation-report.txt"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if REPORT.exists():
        REPORT.unlink()

    driver = ROOT / ".reference_solution.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
