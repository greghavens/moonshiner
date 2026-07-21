"""Durable run, job, and attempt ledger backed by SQLite."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import RUNS

DB_PATH = RUNS / "moonshiner.sqlite3"
RUN_KINDS = {"seed", "trace"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS runs (
      id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      config_json TEXT NOT NULL, limits_json TEXT NOT NULL,
      error TEXT, model_calls INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS jobs (
      run_id TEXT NOT NULL REFERENCES runs(id), seed_id TEXT NOT NULL,
      status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
      last_error TEXT, updated_at TEXT NOT NULL,
      lease_owner TEXT, lease_expires_at TEXT,
      PRIMARY KEY (run_id, seed_id)
    );
    CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
      seed_id TEXT NOT NULL, number INTEGER NOT NULL, status TEXT NOT NULL,
      started_at TEXT NOT NULL, finished_at TEXT, teacher_usage_json TEXT,
      review_json TEXT, error TEXT,
      artifact_path TEXT,
      UNIQUE(run_id, seed_id, number)
    );
    """)
    columns={row[1] for row in db.execute("PRAGMA table_info(attempts)")}
    if "artifact_path" not in columns:
        db.execute("ALTER TABLE attempts ADD COLUMN artifact_path TEXT")
    run_columns={row[1] for row in db.execute("PRAGMA table_info(runs)")}
    if "model_calls" not in run_columns:
        db.execute("ALTER TABLE runs ADD COLUMN model_calls INTEGER NOT NULL DEFAULT 0")
    job_columns={row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    if "lease_owner" not in job_columns:
        db.execute("ALTER TABLE jobs ADD COLUMN lease_owner TEXT")
    if "lease_expires_at" not in job_columns:
        db.execute("ALTER TABLE jobs ADD COLUMN lease_expires_at TEXT")
    db.commit()
    return db


def accepted_attempt_versions(db: sqlite3.Connection, kind: str) -> dict[str, int]:
    """Return latest accepted attempt IDs for exactly one action queue."""
    if kind not in RUN_KINDS:
        raise ValueError(f"unknown run kind: {kind}")
    return {str(row[0]): int(row[1]) for row in db.execute("""
        SELECT a.seed_id,MAX(a.id) FROM attempts a
        JOIN runs r ON r.id=a.run_id
        WHERE r.kind=? AND a.status='accepted' GROUP BY a.seed_id""", (kind,))}


def trace_attempt_counts_for_current_seed_revision(
        db: sqlite3.Connection) -> dict[str, int]:
    """Count trace attempts only after the latest accepted seed revision."""
    return {str(row[0]): int(row[1]) for row in db.execute("""
        SELECT a.seed_id,COUNT(a.id) FROM attempts a
        JOIN runs r ON r.id=a.run_id
        WHERE r.kind='trace' AND a.status IN ('accepted','retry','exhausted')
          AND a.id > COALESCE((SELECT MAX(sa.id) FROM attempts sa
            JOIN runs sr ON sr.id=sa.run_id WHERE sr.kind='seed'
            AND sa.status='accepted' AND sa.seed_id=a.seed_id),0)
        GROUP BY a.seed_id""")}


def live_trace_run_ids(db: sqlite3.Connection) -> set[str]:
    """Return trace runs that currently own at least one unexpired job lease."""
    timestamp = now()
    return {str(row[0]) for row in db.execute("""
        SELECT DISTINCT r.id FROM runs r JOIN jobs j ON j.run_id=r.id
        WHERE r.kind='trace' AND r.status='running' AND j.status='running'
          AND j.lease_expires_at IS NOT NULL AND j.lease_expires_at>?""",
        (timestamp,))}


def create_run(db: sqlite3.Connection, kind: str, config: dict,
               limits: dict, seed_ids: list[str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}"
    timestamp = now()
    db.execute("INSERT INTO runs(id,kind,status,created_at,updated_at,config_json,limits_json,error,model_calls) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0)",
               (run_id, kind, "running", timestamp, timestamp,
                json.dumps(config, sort_keys=True), json.dumps(limits, sort_keys=True)))
    db.executemany("INSERT INTO jobs(run_id,seed_id,status,attempts,last_error,updated_at) "
                   "VALUES (?, ?, 'pending', 0, NULL, ?)",
                   [(run_id, seed_id, timestamp) for seed_id in seed_ids])
    db.commit()
    return run_id


def set_run_status(db, run_id: str, status: str, error: str | None = None) -> None:
    db.execute("UPDATE runs SET status=?, updated_at=?, error=? WHERE id=?",
               (status, now(), error, run_id))
    db.commit()


def record_model_call(db, run_id: str) -> int:
    db.execute("UPDATE runs SET model_calls=model_calls+1, updated_at=? WHERE id=?",
               (now(), run_id))
    db.commit()
    return int(db.execute("SELECT model_calls FROM runs WHERE id=?", (run_id,)).fetchone()[0])


def claim_job(db, run_id: str, owner: str, lease_seconds: int = 120) -> dict | None:
    """Atomically lease one pending/retry job, recovering an expired claim once."""
    timestamp = now()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(
        timespec="seconds")
    db.execute("BEGIN IMMEDIATE")
    expired = db.execute(
        "SELECT seed_id,attempts FROM jobs WHERE run_id=? AND status='running' "
        "AND lease_expires_at IS NOT NULL AND lease_expires_at<=? ORDER BY seed_id LIMIT 1",
        (run_id, timestamp)).fetchone()
    if expired:
        db.execute("UPDATE attempts SET status='abandoned',finished_at=?,error=? "
                   "WHERE run_id=? AND seed_id=? AND status='running'",
                   (timestamp, "worker lease expired", run_id, expired[0]))
        db.execute("UPDATE jobs SET status='retry',last_error=?,lease_owner=NULL,"
                   "lease_expires_at=NULL,updated_at=? WHERE run_id=? AND seed_id=?",
                   ("worker lease expired", timestamp, run_id, expired[0]))
    row = db.execute(
        "SELECT seed_id,attempts,last_error FROM jobs WHERE run_id=? "
        "AND status IN ('pending','retry') ORDER BY seed_id LIMIT 1", (run_id,)).fetchone()
    if row is None:
        db.commit(); return None
    updated = db.execute(
        "UPDATE jobs SET status='running',lease_owner=?,lease_expires_at=?,updated_at=? "
        "WHERE run_id=? AND seed_id=? AND status IN ('pending','retry')",
        (owner, expires, timestamp, run_id, row[0])).rowcount
    if updated != 1:
        db.rollback(); return None
    db.commit()
    return {"seed_id": row[0], "attempts": int(row[1]), "last_error": row[2]}


def renew_lease(db, run_id: str, seed_id: str, owner: str,
                lease_seconds: int = 120) -> bool:
    expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(
        timespec="seconds")
    changed = db.execute(
        "UPDATE jobs SET lease_expires_at=?,updated_at=? WHERE run_id=? AND seed_id=? "
        "AND status='running' AND lease_owner=?",
        (expires, now(), run_id, seed_id, owner)).rowcount
    db.commit()
    return changed == 1


def abandon_claim(db, run_id: str, seed_id: str, owner: str, error: str) -> None:
    """Return a failed worker's claim immediately; no lease timeout is required."""
    timestamp = now()
    db.execute("UPDATE attempts SET status='abandoned',finished_at=?,error=? "
               "WHERE run_id=? AND seed_id=? AND status='running'",
               (timestamp, error, run_id, seed_id))
    db.execute("UPDATE jobs SET status='retry',last_error=?,updated_at=?,"
               "lease_owner=NULL,lease_expires_at=NULL WHERE run_id=? AND seed_id=? "
               "AND status='running' AND lease_owner=?",
               (error, timestamp, run_id, seed_id, owner))
    db.commit()


def set_job(db, run_id: str, seed_id: str, status: str,
            attempts: int, error: str | None = None) -> None:
    db.execute("UPDATE jobs SET status=?, attempts=?, last_error=?, updated_at=?, "
               "lease_owner=NULL,lease_expires_at=NULL "
               "WHERE run_id=? AND seed_id=?",
               (status, attempts, error, now(), run_id, seed_id))
    db.commit()


def start_attempt(db, run_id: str, seed_id: str, number: int) -> None:
    db.execute("INSERT INTO attempts(run_id, seed_id, number, status, started_at) "
               "VALUES (?, ?, ?, 'running', ?)", (run_id, seed_id, number, now()))
    db.execute("UPDATE jobs SET attempts=?,updated_at=? WHERE run_id=? AND seed_id=?",
               (number, now(), run_id, seed_id))
    db.commit()


def finish_attempt(db, run_id: str, seed_id: str, number: int, status: str,
                   usage: dict | None = None, review: dict | None = None,
                   error: str | None = None, artifact_path: str | None = None) -> None:
    db.execute("UPDATE attempts SET status=?, finished_at=?, teacher_usage_json=?, "
               "review_json=?, error=?, artifact_path=? WHERE run_id=? AND seed_id=? AND number=?",
               (status, now(), json.dumps(usage or {}), json.dumps(review or {}),
                error, artifact_path, run_id, seed_id, number))
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

def run_row(db, run_id: str) -> dict | None:
    row=db.execute("SELECT * FROM runs WHERE id=?",(run_id,)).fetchone()
    return dict(row) if row else None


def latest_running_run(db, kind: str) -> dict | None:
    row = db.execute("SELECT * FROM runs WHERE kind=? AND status='running' "
                     "ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone()
    return dict(row) if row else None
