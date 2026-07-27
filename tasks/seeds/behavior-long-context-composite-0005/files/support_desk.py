#!/usr/bin/env python3
"""Executable, SQLite-backed interface to the sandboxed support desk."""

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
AUDIT_LOCK = RUNTIME / "audit.lock"
SEED = ROOT / "support_seed.sql"


def canonical(value: dict[str, object]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def initialize_database() -> None:
    RUNTIME.mkdir(exist_ok=True)
    lock_path = RUNTIME / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if DATABASE.exists():
            return
        temporary = RUNTIME / f"support.{os.getpid()}.sqlite3"
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(SEED.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, DATABASE)


def read_audit_unlocked() -> list[dict[str, object]]:
    if not AUDIT.is_file():
        return []
    return [
        json.loads(line)
        for line in AUDIT.read_text(encoding="utf-8").splitlines()
        if line
    ]


def read_audit() -> list[dict[str, object]]:
    RUNTIME.mkdir(exist_ok=True)
    with AUDIT_LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            return read_audit_unlocked()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def append_audit(entry: dict[str, object]) -> None:
    RUNTIME.mkdir(exist_ok=True)
    with AUDIT_LOCK.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            entries = read_audit_unlocked()
            entry["sequence"] = len(entries) + 1
            entry["previous"] = entries[-1]["digest"] if entries else "0" * 64
            entry["digest"] = hashlib.sha256(canonical(entry)).hexdigest()
            with AUDIT.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        entry,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def record_digest(record: dict[str, object]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def process_started_ticks(pid: int) -> int:
    """Return a process identity component that is stable across PID reuse."""
    stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8")
    closing = stat.rfind(") ")
    if closing < 0:
        raise OSError(f"invalid process stat for pid {pid}")
    fields = stat[closing + 2 :].split()
    return int(fields[19])


def execute(
    operation: str, arguments: dict[str, object]
) -> tuple[dict[str, object], bool]:
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
                "SELECT id, name, location, status, case_date AS date, "
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

        if operation == "profile":
            row = connection.execute(
                "SELECT location, escalation_channel, service_tier "
                "FROM saved_profiles WHERE location = ?",
                (arguments["location"],),
            ).fetchone()
            return ({"error": "profile not found"}, False) if row is None else (
                row_dict(row),
                True,
            )

        if operation == "availability":
            row = connection.execute(
                "SELECT team, shift_date AS date, agents_available "
                "FROM availability WHERE team = ? AND shift_date = ?",
                (arguments["team"], arguments["date"]),
            ).fetchone()
            return ({"available": False}, True) if row is None else (
                {"available": row["agents_available"] > 0, **row_dict(row)},
                True,
            )

        if operation == "create":
            connection.execute(
                "INSERT INTO cases "
                "(id, name, location, case_date, status, priority, owner, summary) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    arguments["id"],
                    arguments["name"],
                    arguments["location"],
                    arguments["date"],
                    arguments["status"],
                    arguments["priority"],
                    arguments["owner"],
                    arguments["summary"],
                ),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) "
                "VALUES ('create', ?, ?)",
                (arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True

        if operation == "update":
            cursor = connection.execute(
                "UPDATE cases SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) "
                "VALUES ('update', ?, ?)",
                (arguments["id"], arguments["status"]),
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
                "VALUES ('cancel', ?, 'cancelled')",
                (arguments["id"],),
            )
            connection.commit()
            return {"cancelled": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "notify":
            connection.execute(
                "INSERT INTO notifications (case_id, message) VALUES (?, ?)",
                (arguments["id"], arguments["message"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, case_id, detail) "
                "VALUES ('notify', ?, ?)",
                (arguments["id"], arguments["message"]),
            )
            connection.commit()
            return {"notified": arguments["id"]}, True

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(
        prog="support_desk.py",
        description="Sandboxed customer-support case desk",
    )
    subcommands = command.add_subparsers(dest="operation", required=True)

    search = subcommands.add_parser(
        "search", help="search by exact case name and exact location"
    )
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = subcommands.add_parser("get", help="retrieve one full case by stable ID")
    get.add_argument("--id", required=True)

    subcommands.add_parser("list", help="list case summaries")

    profile = subcommands.add_parser("profile", help="read a saved customer profile")
    profile.add_argument("--location", required=True)

    availability = subcommands.add_parser(
        "availability", help="check support-team availability"
    )
    availability.add_argument("--team", required=True)
    availability.add_argument("--date", required=True)

    create = subcommands.add_parser("create", help="create a support case")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--date", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--priority", required=True)
    create.add_argument("--owner", required=True)
    create.add_argument("--summary", required=True)

    update = subcommands.add_parser("update", help="update a case status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = subcommands.add_parser("cancel", help="cancel a case")
    cancel.add_argument("--id", required=True)

    notify = subcommands.add_parser("notify", help="send a case notification")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    return command


def main() -> int:
    namespace = parser().parse_args()
    operation = str(namespace.operation)
    arguments = {
        key: value for key, value in vars(namespace).items() if key != "operation"
    }
    started_ns = time.monotonic_ns()
    result: dict[str, object]
    success: bool
    try:
        initialize_database()
        existing = read_audit()
        if operation == "search" and existing:
            raise RuntimeError("the search must be the first and only search")
        if operation == "get":
            if len(existing) != 1:
                raise RuntimeError("get requires exactly one completed search")
            search = existing[0]
            ids = search.get("result_ids")
            if (
                search.get("operation") != "search"
                or search.get("success") is not True
                or not isinstance(ids, list)
                or len(ids) != 1
            ):
                raise RuntimeError("the preceding search did not resolve uniquely")
            if arguments.get("id") != ids[0]:
                raise RuntimeError("get must use the stable ID returned by the search")
        result, success = execute(operation, arguments)
    except (RuntimeError, ValueError, sqlite3.Error, OSError, json.JSONDecodeError) as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        success = False

    finished_ns = time.monotonic_ns()
    audit: dict[str, object] = {
        "operation": operation,
        "arguments": arguments,
        "started_ns": started_ns,
        "finished_ns": finished_ns,
        "pid": os.getpid(),
        "process_started_ticks": process_started_ticks(os.getpid()),
        "parent_pid": os.getppid(),
        "parent_started_ticks": process_started_ticks(os.getppid()),
        "success": success,
    }
    if operation == "search" and success:
        audit["result_ids"] = [
            match["id"] for match in result.get("matches", [])  # type: ignore[index]
        ]
    if operation == "get" and success:
        audit["record_digest"] = record_digest(result)
    append_audit(audit)

    print(
        json.dumps(result, sort_keys=True, ensure_ascii=False),
        file=sys.stdout if success else sys.stderr,
    )
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
