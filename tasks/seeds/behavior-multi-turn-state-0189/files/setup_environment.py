#!/usr/bin/env python3
"""Create a fresh protected CRM session for the task harness."""

from __future__ import annotations

import sqlite3
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "__pycache__" / "crm_session"
DATABASE = RUNTIME / "state.sqlite3"

ACCOUNTS = [
    (
        "ACC-6557",
        "Harbor Youth Center",
        "Northeast Region",
        "2026-08-27",
        "active",
    ),
    (
        "ACC-6558",
        "Harbor Youth Center",
        "Northeast Annex",
        "2026-10-09",
        "pending",
    ),
    (
        "ACC-6559",
        "Harbor Youth Center Archive",
        "Northeast Region",
        "2025-08-27",
        "archived",
    ),
    (
        "ACC-9557",
        "Mesa Trail Outfitters",
        "Southwest Region",
        "2026-09-14",
        "pending",
    ),
]


def initialize() -> None:
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)

    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            PRAGMA synchronous = FULL;

            CREATE TABLE accounts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                next_review_date TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT NOT NULL,
                message TEXT NOT NULL
            );

            CREATE TABLE audit (
                seq INTEGER PRIMARY KEY,
                time_ns INTEGER NOT NULL,
                pid INTEGER NOT NULL,
                ppid INTEGER NOT NULL,
                actor TEXT NOT NULL,
                operation TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                previous_digest TEXT NOT NULL,
                digest TEXT NOT NULL UNIQUE
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO accounts
                (id, name, location, next_review_date, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            ACCOUNTS,
        )
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
