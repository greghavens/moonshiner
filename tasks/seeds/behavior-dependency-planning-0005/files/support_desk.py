#!/usr/bin/env python3
"""Executable, file-backed support desk for the sandboxed case audit."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".support-runtime"
DATABASE = RUNTIME / "support.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
SEED = ROOT / "support_seed.sql"
SYNC = RUNTIME / "sync"


def initialize_database() -> None:
    RUNTIME.mkdir(exist_ok=True)
    lock_path = RUNTIME / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not DATABASE.exists():
            temporary = RUNTIME / f"support.{os.getpid()}.sqlite3"
            connection = sqlite3.connect(temporary)
            try:
                connection.executescript(SEED.read_text(encoding="utf-8"))
                connection.commit()
            finally:
                connection.close()
            os.replace(temporary, DATABASE)


def append_audit(entry: dict[str, object]) -> None:
    encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def resolved_search_branches() -> int:
    """Count successful uniquely resolved searches in the current audit."""
    if not AUDIT.is_file():
        return 0
    resolved = 0
    for line in AUDIT.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        if (
            entry.get("operation") == "search"
            and entry.get("success") is True
            and isinstance(entry.get("result_ids"), list)
            and len(entry["result_ids"]) == 1
        ):
            resolved += 1
    return resolved


def concurrency_barrier(operation: str) -> str:
    """Rendezvous two independent client processes in one operation stage."""
    stage = SYNC / operation
    stage.mkdir(parents=True, exist_ok=True)
    nonce = f"{time.monotonic_ns()}-{os.getpid()}"
    marker = stage / f"{nonce}.ready"
    marker.write_text(nonce + "\n", encoding="utf-8")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        markers = sorted(stage.glob("*.ready"))
        if len(markers) >= 2:
            names = "\n".join(path.name for path in markers[:2])
            return hashlib.sha256(names.encode()).hexdigest()[:20]
        time.sleep(0.02)
    marker.unlink(missing_ok=True)
    raise RuntimeError(f"{operation} requires two concurrent invocations")


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def record_digest(record: dict[str, object]) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def execute(operation: str, arguments: dict[str, object]) -> tuple[dict[str, object], bool]:
    initialize_database()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        if operation == "search":
            rows = connection.execute(
                "SELECT id, name, location FROM cases "
                "WHERE name = ? AND location = ? ORDER BY id",
                (arguments["name"], arguments["location"]),
            ).fetchall()
            matches = [row_dict(row) for row in rows]
            return {"count": len(matches), "matches": matches}, True

        if operation == "get":
            row = connection.execute(
                "SELECT id, name, location, case_date AS date, status, "
                "priority, owner, summary FROM cases WHERE id = ?",
                (arguments["id"],),
            ).fetchone()
            if row is None:
                return {"error": "case not found", "id": arguments["id"]}, False
            return row_dict(row), True

        if operation == "list":
            rows = connection.execute(
                "SELECT id, name, location FROM cases ORDER BY id"
            ).fetchall()
            return {"cases": [row_dict(row) for row in rows]}, True

        if operation == "preferences":
            rows = connection.execute(
                "SELECT preference_key, preference_value FROM saved_preferences "
                "ORDER BY preference_key"
            ).fetchall()
            return {
                str(row["preference_key"]): row["preference_value"] for row in rows
            }, True

        if operation == "availability":
            row = connection.execute(
                "SELECT team, shift_date AS date, agents_available FROM availability "
                "WHERE team = ? AND shift_date = ?",
                (arguments["team"], arguments["date"]),
            ).fetchone()
            return ({"available": False} if row is None
                    else {"available": row["agents_available"] > 0, **row_dict(row)}), True

        if operation == "create":
            connection.execute(
                "INSERT INTO cases "
                "(id, name, location, case_date, status, priority, owner, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (arguments["id"], arguments["name"], arguments["location"],
                 arguments["date"], arguments["status"], arguments["priority"],
                 arguments["owner"], arguments["summary"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True

        if operation == "update":
            cursor = connection.execute(
                "UPDATE cases SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"updated": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "cancel":
            cursor = connection.execute(
                "UPDATE cases SET status = 'cancelled' WHERE id = ?",
                (arguments["id"],),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) "
                "VALUES (?, ?, 'cancelled')",
                (operation, arguments["id"]),
            )
            connection.commit()
            return {"cancelled": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "notify":
            connection.execute(
                "INSERT INTO notifications (case_id, message) VALUES (?, ?)",
                (arguments["id"], arguments["message"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["message"]),
            )
            connection.commit()
            return {"notified": arguments["id"]}, True

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Sandboxed customer-support case desk")
    commands = main.add_subparsers(dest="operation", required=True)

    search = commands.add_parser("search", help="search by exact case name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one full case by stable ID")
    get.add_argument("--id", required=True)

    commands.add_parser("list", help="list case summaries")
    commands.add_parser("preferences", help="read saved support preferences")

    availability = commands.add_parser("availability", help="check support availability")
    availability.add_argument("--team", required=True)
    availability.add_argument("--date", required=True)

    create = commands.add_parser("create", help="create a case")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--date", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--priority", required=True)
    create.add_argument("--owner", required=True)
    create.add_argument("--summary", required=True)

    update = commands.add_parser("update", help="update a case status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = commands.add_parser("cancel", help="cancel a case")
    cancel.add_argument("--id", required=True)

    notify = commands.add_parser("notify", help="record a case notification")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    return main


def main() -> int:
    namespace = parser().parse_args()
    operation = namespace.operation
    arguments = {
        key: value for key, value in vars(namespace).items() if key != "operation"
    }
    started = time.monotonic_ns()
    concurrency_batch: str | None = None
    try:
        if operation == "search":
            concurrency_batch = concurrency_barrier(operation)
        elif operation == "get" and resolved_search_branches() >= 2:
            concurrency_batch = concurrency_barrier(operation)
        result, success = execute(operation, arguments)
        if operation in {"search", "get"}:
            time.sleep(0.30)
    except Exception as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        success = False
    finished = time.monotonic_ns()

    audit: dict[str, object] = {
        "operation": operation,
        "arguments": arguments,
        "started_ns": started,
        "finished_ns": finished,
        "parent_pid": os.getppid(),
        "success": success,
        "concurrency_batch": concurrency_batch,
    }
    if operation == "search" and success:
        audit["result_ids"] = [match["id"] for match in result["matches"]]
    if operation == "get" and success:
        audit["record_digest"] = record_digest(result)
    append_audit(audit)
    stream = sys.stdout if success else sys.stderr
    print(json.dumps(result, sort_keys=True), file=stream)
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
