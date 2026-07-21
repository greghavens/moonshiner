#!/usr/bin/env python3
"""Initialize the disposable outbound-message sandbox for one trace."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".message-runtime"
DATABASE = RUNTIME / "messages.sqlite3"
SEED = ROOT / ".protected" / "messages_seed.sql"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.execute("PRAGMA journal_mode = WAL")
        database.commit()
    finally:
        database.close()

    # Model-free validation applies the reference patch before setup. The
    # driver then proves the result through the same executable interface.
    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
