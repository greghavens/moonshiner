#!/usr/bin/env python3
"""Reset the sandboxed course-register runtime."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / ".protected" / "courses.json"
RUNTIME = ROOT / ".course-runtime"
DATABASE = RUNTIME / "register.sqlite3"
DELIVERABLE = ROOT / "course-details.md"


def main() -> int:
    payload = json.loads(SEED.read_text(encoding="utf-8"))
    records = payload["records"]
    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    if DELIVERABLE.exists():
        DELIVERABLE.unlink()
    RUNTIME.mkdir()
    connection = sqlite3.connect(DATABASE)
    try:
        connection.executescript(
            """
            CREATE TABLE courses (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                campus TEXT NOT NULL,
                status TEXT NOT NULL,
                detail_json TEXT NOT NULL
            );
            CREATE TABLE notifications (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        import hashlib

        seed_hash = hashlib.sha256(SEED.read_bytes()).hexdigest()
        connection.executemany(
            """
            INSERT INTO courses(id, name, campus, status, detail_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    record["id"],
                    record["name"],
                    record["campus"],
                    record["status"],
                    json.dumps(
                        record,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                for record in records
            ],
        )
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('seed_sha256', ?)",
            (seed_hash,),
        )
        connection.commit()
    finally:
        connection.close()
    (RUNTIME / "audit.jsonl").write_text("", encoding="utf-8")
    (RUNTIME / ".lock").write_text("", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
