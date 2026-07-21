#!/usr/bin/env python3
"""Initialize the disposable customer-case sandbox for one trace."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".case-runtime"
DATABASE = RUNTIME / "cases.sqlite3"
SEED = ROOT / ".protected" / "cases_seed.sql"
RECEIPT = ROOT / "case-handoff.receipt.json"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    if RECEIPT.exists():
        RECEIPT.unlink()

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
