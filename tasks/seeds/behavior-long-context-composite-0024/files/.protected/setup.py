#!/usr/bin/env python3
"""Initialize the disposable message-register environment."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".register-runtime"
DATABASE = RUNTIME / "messages.sqlite3"
SEED = ROOT / ".protected" / "messages_seed.sql"
RECEIPT = ROOT / ".register-receipt.json"


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

    reference = ROOT / "reference_driver.py"
    if reference.is_file():
        subprocess.run(
            [sys.executable, "-B", str(reference)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
