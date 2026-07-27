#!/usr/bin/env python3
"""Initialize the disposable library sandbox and optional proof run."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".library-runtime"
DATABASE = RUNTIME / "library.sqlite3"
SEED = ROOT / ".protected" / "library_seed.sql"
DELIVERABLE = ROOT / "cancellation-report.md"
REFERENCE_DRIVER = ROOT / ".reference_solution.py"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    if DELIVERABLE.exists():
        DELIVERABLE.unlink()

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.commit()
    finally:
        database.close()
    DATABASE.chmod(0o600)

    if REFERENCE_DRIVER.is_file():
        subprocess.run(
            [sys.executable, "-B", str(REFERENCE_DRIVER)],
            cwd=ROOT,
            check=True,
            timeout=15,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
