#!/usr/bin/env python3
"""Prepare the disposable local shipment database for a trace."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import subprocess


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
DATABASE = STATE / "shipments.sqlite3"
HISTORY = STATE / "operations.jsonl"
FIXTURE = Path(__file__).with_name("records.json")


def main() -> None:
    STATE.mkdir(exist_ok=True)
    for path in STATE.iterdir():
        if path.is_file():
            path.unlink()

    records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode = DELETE;
            CREATE TABLE shipments (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                city TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE notifications (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                shipment_id TEXT NOT NULL,
                message TEXT NOT NULL
            );
            """
        )
        connection.executemany(
            "INSERT INTO shipments(id, name, city, status) VALUES (?, ?, ?, ?)",
            [(r["id"], r["name"], r["city"], r["status"]) for r in records],
        )
        connection.commit()
    finally:
        connection.close()
    HISTORY.touch()

    reference = ROOT / ".reference_solution.sh"
    if reference.is_file():
        subprocess.run(["bash", str(reference)], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
