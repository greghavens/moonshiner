#!/usr/bin/env python3
"""Reset generated calendar state and run an applied reference proof."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".calendar-runtime"
REFERENCE_PROOF = RUNTIME / ".reference-setup-complete"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)

    driver = ROOT / ".reference_solution.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
        REFERENCE_PROOF.write_text("reference setup completed\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
