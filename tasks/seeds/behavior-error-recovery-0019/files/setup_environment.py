#!/usr/bin/env python3
"""Reset the deterministic permit environment and run a reference proof if present."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".permit-state.json"
SEED = ROOT / "data" / "seed.json"


def main() -> int:
    shutil.copyfile(SEED, STATE)
    reference_driver = ROOT / "reference_driver.py"
    if reference_driver.is_file():
        subprocess.run(
            [sys.executable, "-B", str(reference_driver)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
