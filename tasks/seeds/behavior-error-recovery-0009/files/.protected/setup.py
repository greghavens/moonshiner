#!/usr/bin/env python3
"""Initialize the disposable CRM sandbox for one operation trace."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".crm-runtime"
DATABASE = RUNTIME / "crm.sqlite3"
SEED = ROOT / ".protected" / "crm_seed.sql"
RECEIPT = ROOT / "cancellation.receipt.json"
REPORT = ROOT / "cancellation-result.txt"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    for generated in (RECEIPT, REPORT):
        if generated.exists():
            generated.unlink()

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.commit()
    finally:
        database.close()

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
