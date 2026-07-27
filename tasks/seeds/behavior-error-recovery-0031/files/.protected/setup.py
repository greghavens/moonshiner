#!/usr/bin/env python3
"""Initialize one disposable recruiting trace from protected source data."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".recruiting"
SEED = ROOT / ".protected" / "recruiting_seed.json"
STATE = RUNTIME / "state.json"
LOCK = RUNTIME / "state.lock"
REPORT = ROOT / "cancellation-report.txt"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    shutil.copyfile(SEED, STATE)
    LOCK.touch(mode=0o600)
    if REPORT.exists():
        REPORT.unlink()

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
