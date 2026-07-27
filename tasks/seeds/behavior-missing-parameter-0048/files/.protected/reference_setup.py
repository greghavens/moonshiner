#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REFERENCE_SOLUTION = ROOT / ".reference-solution.py"


def main() -> int:
    if not REFERENCE_SOLUTION.is_file():
        return 0
    subprocess.run(
        [sys.executable, "-B", str(REFERENCE_SOLUTION)],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
