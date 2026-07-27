#!/usr/bin/env python3
"""Create a fresh protected library session for the task harness."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import subprocess


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".library-runtime"
DATABASE = RUNTIME / "catalog.sqlite3"
SEED_SQL = ROOT / ".protected" / "catalog_seed.sql"


def initialize() -> None:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript(SEED_SQL.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()


def main() -> int:
    initialize()
    reference = ROOT / "reference_solution.sh"
    if reference.is_file():
        subprocess.run(["bash", str(reference)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
