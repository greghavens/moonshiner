#!/usr/bin/env python3
"""Executable, file-backed lodging desk for the sandboxed audit task."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / ".lodging-runtime"
DATABASE = RUNTIME / "lodging.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
SEED = ROOT / "lodging_seed.sql"


def initialize_database() -> None:
    RUNTIME.mkdir(exist_ok=True)
    lock_path = RUNTIME / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not DATABASE.exists():
            temporary = RUNTIME / f"lodging.{os.getpid()}.sqlite3"
            connection = sqlite3.connect(temporary)
            try:
                connection.executescript(SEED.read_text(encoding="utf-8"))
                connection.commit()
            finally:
                connection.close()
            os.replace(temporary, DATABASE)


def append_audit(entry: dict) -> None:
    encoded = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, encoded)
        os.fsync(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def execute(operation: str, arguments: dict) -> tuple[dict, bool]:
    initialize_database()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        if operation == "search":
            rows = connection.execute(
                "SELECT id, name, location FROM reservations "
                "WHERE name = ? AND location = ? ORDER BY id",
                (arguments["name"], arguments["location"]),
            ).fetchall()
            matches = [row_dict(row) for row in rows]
            return {"count": len(matches), "matches": matches}, True

        if operation == "get":
            row = connection.execute(
                "SELECT id, name, location, stay_date AS date, status, "
                "property, rooms, notes FROM reservations WHERE id = ?",
                (arguments["id"],),
            ).fetchone()
            if row is None:
                return {"error": "reservation not found", "id": arguments["id"]}, False
            return row_dict(row), True

        if operation == "list":
            rows = connection.execute(
                "SELECT id, name, location FROM reservations ORDER BY id"
            ).fetchall()
            return {"reservations": [row_dict(row) for row in rows]}, True

        if operation == "preferences":
            rows = connection.execute(
                "SELECT preference_key, preference_value FROM saved_preferences "
                "ORDER BY preference_key"
            ).fetchall()
            return {row["preference_key"]: row["preference_value"] for row in rows}, True

        if operation == "availability":
            row = connection.execute(
                "SELECT location, stay_date AS date, rooms_available FROM availability "
                "WHERE location = ? AND stay_date = ?",
                (arguments["location"], arguments["date"]),
            ).fetchone()
            return ({"available": False} if row is None
                    else {"available": row["rooms_available"] > 0, **row_dict(row)}), True

        if operation == "create":
            connection.execute(
                "INSERT INTO reservations "
                "(id, name, location, stay_date, status, property, rooms, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (arguments["id"], arguments["name"], arguments["location"],
                 arguments["date"], arguments["status"], arguments["property"],
                 arguments["rooms"], arguments["notes"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, reservation_id, detail) "
                "VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True

        if operation == "update":
            cursor = connection.execute(
                "UPDATE reservations SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, reservation_id, detail) "
                "VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"updated": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "cancel":
            cursor = connection.execute(
                "UPDATE reservations SET status = 'cancelled' WHERE id = ?",
                (arguments["id"],),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, reservation_id, detail) "
                "VALUES (?, ?, 'cancelled')",
                (operation, arguments["id"]),
            )
            connection.commit()
            return {"cancelled": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "notify":
            connection.execute(
                "INSERT INTO notifications (reservation_id, message) VALUES (?, ?)",
                (arguments["id"], arguments["message"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, reservation_id, detail) "
                "VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["message"]),
            )
            connection.commit()
            return {"notified": arguments["id"]}, True

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Sandboxed lodging reservation desk")
    commands = main.add_subparsers(dest="operation", required=True)

    search = commands.add_parser("search", help="search by exact name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one full reservation by stable ID")
    get.add_argument("--id", required=True)

    commands.add_parser("list", help="list reservation summaries")
    commands.add_parser("preferences", help="read saved lodging preferences")

    availability = commands.add_parser("availability", help="check room availability")
    availability.add_argument("--location", required=True)
    availability.add_argument("--date", required=True)

    create = commands.add_parser("create", help="create a reservation")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--date", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--property", required=True)
    create.add_argument("--rooms", required=True, type=int)
    create.add_argument("--notes", required=True)

    update = commands.add_parser("update", help="update a reservation status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = commands.add_parser("cancel", help="cancel a reservation")
    cancel.add_argument("--id", required=True)

    notify = commands.add_parser("notify", help="record a reservation notification")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    return main


def main() -> int:
    namespace = parser().parse_args()
    operation = namespace.operation
    arguments = {key: value for key, value in vars(namespace).items()
                 if key != "operation"}
    started = time.monotonic_ns()
    try:
        result, success = execute(operation, arguments)
        if operation in {"search", "get"}:
            time.sleep(0.30)
    except Exception as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        success = False
    finished = time.monotonic_ns()

    audit = {
        "operation": operation,
        "arguments": arguments,
        "started_ns": started,
        "finished_ns": finished,
        "parent_pid": os.getppid(),
        "success": success,
    }
    if operation == "search" and success:
        audit["result_ids"] = [match["id"] for match in result["matches"]]
    append_audit(audit)
    stream = sys.stdout if success else sys.stderr
    print(json.dumps(result, sort_keys=True), file=stream)
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
