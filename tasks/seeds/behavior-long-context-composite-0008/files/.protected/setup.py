#!/usr/bin/env python3
"""Initialize the disposable shipment sandbox for one trace."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".shipment-runtime"
DATABASE = RUNTIME / "shipments.sqlite3"
AUDIT = RUNTIME / "execution.sqlite3"
SEED = ROOT / ".protected" / "shipments_seed.sql"
RECEIPT = ROOT / "shipment-audit.receipt.json"


def main() -> int:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(mode=0o700)
    if RECEIPT.exists():
        RECEIPT.unlink()

    database = sqlite3.connect(DATABASE)
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        database.commit()
    finally:
        database.close()

    baseline_sha256 = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    audit = sqlite3.connect(AUDIT)
    try:
        audit.executescript(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE operation_journal (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                started_ns INTEGER NOT NULL,
                finished_ns INTEGER,
                pid INTEGER NOT NULL,
                parent_pid INTEGER NOT NULL,
                action_id TEXT NOT NULL,
                result_count INTEGER,
                sole_id TEXT,
                result_digest TEXT,
                violation INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """
        )
        audit.execute(
            "INSERT INTO metadata (key, value) VALUES ('baseline_sha256', ?)",
            (baseline_sha256,),
        )
        audit.commit()
    finally:
        audit.close()

    driver = ROOT / "reference_driver.py"
    if driver.is_file():
        subprocess.run([sys.executable, "-B", str(driver)], cwd=ROOT, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
