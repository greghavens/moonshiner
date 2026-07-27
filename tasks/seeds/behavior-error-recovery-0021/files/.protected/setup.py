#!/usr/bin/env python3
"""Initialize one disposable calendar trace from protected source data."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / ".protected" / "calendar_seed.json"
STATE = ROOT / ".calendar-state.json"
LOCK = ROOT / ".calendar-state.lock"


def main() -> int:
    LOCK.unlink(missing_ok=True)
    shutil.copyfile(SEED, STATE)

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
