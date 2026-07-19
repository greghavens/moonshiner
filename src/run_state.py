"""Durable run, job, and attempt ledger backed by SQLite."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from common import RUNS

DB_PATH = RUNS / "moonshiner.sqlite3"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      config_json TEXT NOT NULL, limits_json TEXT NOT NULL,
      error TEXT
    );
    CREATE TABLE IF NOT EXISTS jobs (
      run_id TEXT NOT NULL REFERENCES runs(id), seed_id TEXT NOT NULL,
      status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT, updated_at TEXT NOT NULL,
      PRIMARY KEY (run_id, seed_id)
    );
    CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
      seed_id TEXT NOT NULL, number INTEGER NOT NULL, status TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT, teacher_usage_json TEXT,
      review_json TEXT, error TEXT,
      UNIQUE(run_id, seed_id, number)
    );
    """)
    return db


def create_run(db: sqlite3.Connection, kind: str, config: dict,
               limits: dict, seed_ids: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}"
    timestamp = now()
    db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
               (run_id, kind, "running", timestamp, timestamp,
                json.dumps(config, sort_keys=True), json.dumps(limits, sort_keys=True)))
    db.executemany("INSERT INTO jobs VALUES (?, ?, 'pending', 0, NULL, ?)",
                   [(run_id, seed_id, timestamp) for seed_id in seed_ids])
    db.commit()
    return run_id


def set_run_status(db, run_id: str, status: str, error: str | None = None) -> None:
    db.execute("UPDATE runs SET status=?, updated_at=?, error=? WHERE id=?",
               (status, now(), error, run_id))
    db.commit()


def set_job(db, run_id: str, seed_id: str, status: str,
            attempts: int, error: str | None = None) -> None:
    db.execute("UPDATE jobs SET status=?, attempts=?, last_error=?, updated_at=? "
               "WHERE run_id=? AND seed_id=?",
               (status, attempts, error, now(), run_id, seed_id))
    db.commit()


def start_attempt(db, run_id: str, seed_id: str, number: int) -> None:
    db.execute("INSERT INTO attempts(run_id, seed_id, number, status, started_at) "
               "VALUES (?, ?, ?, 'running', ?)", (run_id, seed_id, number, now()))
    set_job(db, run_id, seed_id, "running", number)


def finish_attempt(db, run_id: str, seed_id: str, number: int, status: str,
                   usage: dict | None = None, review: dict | None = None,
                   error: str | None = None) -> None:
    db.execute("UPDATE attempts SET status=?, finished_at=?, teacher_usage_json=?, "
               "review_json=?, error=? WHERE run_id=? AND seed_id=? AND number=?",
               (status, now(), json.dumps(usage or {}), json.dumps(review or {}),
                error, run_id, seed_id, number))
    set_job(db, run_id, seed_id, status, number, error)


def summaries(db, run_id: str | None = None) -> list[dict]:
    where, args = ("WHERE r.id=?", (run_id,)) if run_id else ("", ())
    rows = db.execute(f"""SELECT r.*, COUNT(j.seed_id) jobs,
      SUM(CASE WHEN j.status='accepted' THEN 1 ELSE 0 END) accepted,
      SUM(CASE WHEN j.status IN ('exhausted','failed') THEN 1 ELSE 0 END) failed,
      SUM(CASE WHEN j.status IN ('pending','running','retry') THEN 1 ELSE 0 END) pending
      FROM runs r LEFT JOIN jobs j ON j.run_id=r.id {where}
      GROUP BY r.id ORDER BY r.created_at DESC""", args).fetchall()
    return [dict(row) for row in rows]


def job_rows(db, run_id: str) -> list[dict]:
    return [dict(row) for row in db.execute(
        "SELECT * FROM jobs WHERE run_id=? ORDER BY seed_id", (run_id,))]
