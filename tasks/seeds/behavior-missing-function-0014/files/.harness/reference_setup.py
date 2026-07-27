#!/usr/bin/env python3
"""Run the reference interaction only after its patch has been applied."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / ".reference_solution.py"


def main() -> int:
    if not REFERENCE.is_file():
        return 0
    subprocess.run(
        [sys.executable, "-B", str(REFERENCE)],
        cwd=ROOT,
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
