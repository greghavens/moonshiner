#!/usr/bin/env python3
"""Initialize a fresh disposable CRM register for one evaluation trace."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".crm-runtime"
DATABASE = RUNTIME / "accounts.sqlite3"
SEED = ROOT / ".protected" / "accounts_seed.sql"
REPORT = ROOT / "account-details.md"


def main() -> int:
    REPORT.unlink(missing_ok=True)
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.commit()
        database.execute("PRAGMA journal_mode = WAL")
    finally:
        database.close()

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
