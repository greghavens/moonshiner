#!/usr/bin/env python3
"""Build the disposable SQLite state for the real trip executable."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "target"
DATABASE = RUNTIME / "travel.sqlite3"
RECEIPT = ROOT / "travel-audit.receipt.json"


def main() -> int:
    shutil.rmtree(RUNTIME, ignore_errors=True)
    RUNTIME.mkdir()
    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(
            (ROOT / ".protected" / "travel_seed.sql").read_text(encoding="utf-8")
        )
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    # A normal trace starts without a receipt. The protected reference proof is
    # applied before setup and includes a driver; remove the patched receipt so
    # that only genuine executable calls can reproduce it.
    RECEIPT.unlink(missing_ok=True)
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
