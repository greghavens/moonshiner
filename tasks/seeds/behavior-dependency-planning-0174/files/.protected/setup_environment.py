#!/usr/bin/env python3
"""Create disposable expense state for the workspace executable."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "__pycache__"
DATABASE = RUNTIME / "expenses.sqlite3"


def main() -> int:
    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir()
    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(
            (ROOT / ".protected" / "expenses.sql").read_text(encoding="utf-8")
        )
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    # The reference validator applies reference_fix.patch before setup. The
    # driver exists only in that patched workspace and exercises expensectl.
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
