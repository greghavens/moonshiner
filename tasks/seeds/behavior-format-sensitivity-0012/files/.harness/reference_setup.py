#!/usr/bin/env python3
"""Create disposable state for the genuine local course archive executable."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / ".courses"
DATABASE = ARCHIVE / "runtime.sqlite3"


def main() -> int:
    for suffix in ("", "-shm", "-wal"):
        path = Path(str(DATABASE) + suffix)
        path.unlink(missing_ok=True)

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(
            (ARCHIVE / "seed.sql").read_text(encoding="utf-8")
        )
        database.commit()
    finally:
        database.close()

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
