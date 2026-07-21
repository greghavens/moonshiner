#!/usr/bin/env python3
"""Executable, file-backed candidate desk for the recruiting sandbox."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parent
SEED = ROOT / "candidate_seed.sql"
CACHE = ROOT / ".pytest_cache" / "candidate-desk"
DATABASE = CACHE / "candidates.sqlite3"
AUDIT_LOG = CACHE / "events.jsonl"
SYNC_ROOT = CACHE / "sync"
AUDIT_KEY = b"candidate-desk-executable-audit-v1"
DATA_OPERATIONS = {
    "search", "get", "list", "preferences", "availability",
    "create", "update", "cancel", "notify",
}


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature(value: dict) -> str:
    return hmac.new(AUDIT_KEY, canonical(value), hashlib.sha256).hexdigest()


def append_audit(event: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    signed = dict(event)
    signed["signature"] = signature(event)
    payload = canonical(signed) + b"\n"
    descriptor = os.open(AUDIT_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, payload)
        os.fsync(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def initialize_database() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    lock_path = CACHE / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if DATABASE.exists():
            return
        temporary = CACHE / f"candidates-{os.getpid()}.sqlite3"
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(SEED.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, DATABASE)


def connect(*, writable: bool = False) -> sqlite3.Connection:
    initialize_database()
    if writable:
        connection = sqlite3.connect(DATABASE, timeout=5)
    else:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    return connection


def verified_events() -> list[dict]:
    if not AUDIT_LOG.exists():
        return []
    events: list[dict] = []
    for line in AUDIT_LOG.read_bytes().splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            supplied = event.pop("signature")
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        if hmac.compare_digest(str(supplied), signature(event)):
            events.append(event)
    return events


def unique_search_returned(candidate_id: str) -> bool:
    for event in verified_events():
        if event.get("operation") != "search" or event.get("ok") is not True:
            continue
        evidence = event.get("evidence") or {}
        if evidence.get("match_count") == 1 and evidence.get("stable_ids") == [candidate_id]:
            return True
    return False


def concurrency_barrier(operation: str) -> str:
    """Require two live processes for each independent read stage."""
    stage = SYNC_ROOT / operation
    stage.mkdir(parents=True, exist_ok=True)
    marker = stage / f"{time.monotonic_ns()}-{os.getpid()}.ready"
    marker.write_text(f"{os.getpid()}\n", encoding="utf-8")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        markers = sorted(stage.glob("*.ready"))
        if len(markers) >= 2:
            joined = "\n".join(path.name for path in markers[:2])
            return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:20]
        time.sleep(0.02)
    marker.unlink(missing_ok=True)
    raise RuntimeError(f"{operation} requires two concurrent invocations")


def row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def execute(args: argparse.Namespace) -> tuple[dict, dict]:
    operation = args.operation
    if operation == "search":
        with connect() as connection:
            rows = connection.execute(
                "SELECT id, name, location FROM candidates "
                "WHERE name = ? AND location = ? ORDER BY id",
                (args.name, args.location),
            ).fetchall()
        matches = [row_dict(row) for row in rows]
        return (
            {"count": len(matches), "matches": matches},
            {
                "name": args.name,
                "location": args.location,
                "match_count": len(matches),
                "stable_ids": [row["id"] for row in rows],
            },
        )

    if operation == "get":
        if not unique_search_returned(args.candidate_id):
            raise RuntimeError("get requires a completed unique search returning this stable ID")
        with connect() as connection:
            row = connection.execute(
                "SELECT id, name, location, status, interview_date, coordinator, notes "
                "FROM candidates WHERE id = ?",
                (args.candidate_id,),
            ).fetchone()
        if row is None:
            raise LookupError("candidate not found")
        record = row_dict(row)
        digest = hashlib.sha256(canonical(record)).hexdigest()
        return record, {"stable_id": args.candidate_id, "record_digest": digest}

    if operation == "list":
        with connect() as connection:
            rows = connection.execute(
                "SELECT id, name, location FROM candidates ORDER BY id"
            ).fetchall()
        records = [row_dict(row) for row in rows]
        return {"candidates": records}, {"row_count": len(records)}

    if operation == "preferences":
        with connect() as connection:
            rows = connection.execute(
                "SELECT preference_key, preference_value FROM saved_preferences "
                "ORDER BY preference_key"
            ).fetchall()
        values = {row["preference_key"]: row["preference_value"] for row in rows}
        return {"preferences": values}, {"row_count": len(values)}

    if operation == "availability":
        with connect() as connection:
            row = connection.execute(
                "SELECT location, interview_date, open_slots FROM availability "
                "WHERE location = ? AND interview_date = ?",
                (args.location, args.date),
            ).fetchone()
        value = None if row is None else row_dict(row)
        return {"availability": value}, {"found": value is not None}

    if operation == "create":
        with connect(writable=True) as connection:
            connection.execute(
                "INSERT INTO candidates "
                "(id, name, location, status, interview_date, coordinator, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (args.candidate_id, args.name, args.location, args.status,
                 args.date, args.coordinator, args.notes),
            )
            connection.execute(
                "INSERT INTO mutation_log(operation, candidate_id, detail) VALUES (?, ?, ?)",
                (operation, args.candidate_id, args.status),
            )
            connection.commit()
        return {"created": args.candidate_id}, {"stable_id": args.candidate_id}

    if operation == "update":
        with connect(writable=True) as connection:
            changed = connection.execute(
                "UPDATE candidates SET status = ? WHERE id = ?",
                (args.status, args.candidate_id),
            ).rowcount
            connection.execute(
                "INSERT INTO mutation_log(operation, candidate_id, detail) VALUES (?, ?, ?)",
                (operation, args.candidate_id, args.status),
            )
            connection.commit()
        return {"updated": changed}, {"stable_id": args.candidate_id, "changed": changed}

    if operation == "cancel":
        with connect(writable=True) as connection:
            changed = connection.execute(
                "UPDATE candidates SET status = 'cancelled' WHERE id = ?",
                (args.candidate_id,),
            ).rowcount
            connection.execute(
                "INSERT INTO mutation_log(operation, candidate_id, detail) VALUES (?, ?, 'cancelled')",
                (operation, args.candidate_id),
            )
            connection.commit()
        return {"cancelled": changed}, {"stable_id": args.candidate_id, "changed": changed}

    if operation == "notify":
        with connect(writable=True) as connection:
            cursor = connection.execute(
                "INSERT INTO notifications(candidate_id, message) VALUES (?, ?)",
                (args.candidate_id, args.message),
            )
            connection.execute(
                "INSERT INTO mutation_log(operation, candidate_id, detail) VALUES (?, ?, ?)",
                (operation, args.candidate_id, args.message),
            )
            connection.commit()
        return {"notification_id": cursor.lastrowid}, {"stable_id": args.candidate_id}

    raise AssertionError(f"unsupported operation: {operation}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="candidate_desk.py",
        description="Query or manage the sandboxed recruiting candidate store.",
    )
    commands = parser.add_subparsers(dest="operation", required=True)

    search = commands.add_parser("search", help="search exact candidate name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one full candidate by stable ID")
    get.add_argument("--id", dest="candidate_id", required=True)

    commands.add_parser("list", help="list candidate summaries")
    commands.add_parser("preferences", help="read saved recruiting preferences")

    availability = commands.add_parser("availability", help="check interview availability")
    availability.add_argument("--location", required=True)
    availability.add_argument("--date", required=True)

    create = commands.add_parser("create", help="create a candidate")
    create.add_argument("--id", dest="candidate_id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--date", required=True)
    create.add_argument("--coordinator", required=True)
    create.add_argument("--notes", required=True)

    update = commands.add_parser("update", help="update candidate status")
    update.add_argument("--id", dest="candidate_id", required=True)
    update.add_argument("--status", required=True)

    cancel = commands.add_parser("cancel", help="cancel a candidacy")
    cancel.add_argument("--id", dest="candidate_id", required=True)

    notify = commands.add_parser("notify", help="record a candidate notification")
    notify.add_argument("--id", dest="candidate_id", required=True)
    notify.add_argument("--message", required=True)
    return parser


def arguments_for(args: argparse.Namespace) -> dict:
    return {key: value for key, value in vars(args).items() if key != "operation"}


def main() -> int:
    args = build_parser().parse_args()
    operation = args.operation
    arguments = arguments_for(args)
    started_ns = time.monotonic_ns()
    batch: str | None = None
    try:
        if operation in {"search", "get"}:
            batch = concurrency_barrier(operation)
            time.sleep(0.25)
        result, evidence = execute(args)
        ok = True
    except (LookupError, OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        evidence = {"error_type": type(error).__name__}
        ok = False
    ended_ns = time.monotonic_ns()
    if operation in DATA_OPERATIONS:
        append_audit({
            "version": 1,
            "operation": operation,
            "arguments": arguments,
            "parent_pid": os.getppid(),
            "pid": os.getpid(),
            "started_ns": started_ns,
            "ended_ns": ended_ns,
            "concurrency_batch": batch,
            "ok": ok,
            "evidence": evidence,
        })
    print(json.dumps(result, sort_keys=True), file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
