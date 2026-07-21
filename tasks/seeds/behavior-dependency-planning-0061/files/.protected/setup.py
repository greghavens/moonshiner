#!/usr/bin/env python3
"""Build the deterministic calendar database and clear prior task artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = Path(__file__).resolve().with_name("meetings_seed.sql")
DATABASE_PATH = ROOT / "calendar.db"
CLIENT_PATH = ROOT / "meetingctl"
RUNTIME_PATH = ROOT / ".meeting-audit"
DELIVERABLE_PATH = ROOT / "meeting-status.txt"
REFERENCE_DRIVER_PATH = ROOT / "reference_driver.py"


def main() -> int:
    temporary = ROOT / f".calendar-setup-{os.getpid()}.sqlite3"
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError("generated calendar database failed integrity check")
        connection.commit()
    finally:
        connection.close()
    os.replace(temporary, DATABASE_PATH)
    shutil.rmtree(RUNTIME_PATH, ignore_errors=True)
    DELIVERABLE_PATH.unlink(missing_ok=True)
    CLIENT_PATH.chmod(0o755)
    if REFERENCE_DRIVER_PATH.is_file():
        subprocess.run(
            [sys.executable, "-B", str(REFERENCE_DRIVER_PATH)],
            cwd=ROOT,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
