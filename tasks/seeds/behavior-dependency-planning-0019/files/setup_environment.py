#!/usr/bin/env python3
"""Build the disposable SQLite service state used by the executable harness."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "__pycache__"
DATABASE = RUNTIME / "permit_queue.sqlite3"


def main() -> int:
    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir()
    database = sqlite3.connect(DATABASE)
    try:
        database.executescript((ROOT / "applications.sql").read_text(encoding="utf-8"))
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    # The local seed validator applies the reference patch before setup.  The
    # driver exists only in that patched workspace and proves the same real
    # executable workflow that the task asks the agent to perform.
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
