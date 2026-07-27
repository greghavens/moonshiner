#!/usr/bin/env python3
"""Reset the disposable claim sandbox and run a patched reference proof."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".claim-runtime"
DATABASE = RUNTIME / "claims.sqlite3"
SEED = ROOT / ".protected" / "claim_seed.sql"
RECEIPT = ROOT / ".claim-audit.receipt.json"
REPORT = ROOT / "claim-availability-report.txt"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    for path in (RECEIPT, REPORT):
        if path.exists():
            path.unlink()

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
