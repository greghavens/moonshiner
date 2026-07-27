#!/usr/bin/env python3
"""Reset generated state and exercise an applied reference driver if present."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".fleet-runtime"
DELIVERABLE = ROOT / "availability-report.md"
REFERENCE_DRIVER = ROOT / ".reference_solution.py"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    DELIVERABLE.unlink(missing_ok=True)
    if REFERENCE_DRIVER.is_file():
        subprocess.run(
            [sys.executable, "-B", str(REFERENCE_DRIVER)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
