#!/usr/bin/env python3
"""Run the reference proof after the reference patch has been applied."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "reference_driver.py"


def main() -> int:
    if DRIVER.is_file():
        subprocess.run(
            [sys.executable, "-B", str(DRIVER)],
            cwd=ROOT,
            check=True,
            timeout=20,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
