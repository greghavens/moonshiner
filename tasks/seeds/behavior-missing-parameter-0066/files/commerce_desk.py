#!/usr/bin/env python3
"""SQLite-backed commerce desk for the fulfillment-location task."""

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
RUNTIME = ROOT / ".commerce-runtime"
DATABASE = RUNTIME / "commerce.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
SEED = ROOT / "commerce_seed.sql"


def initialize_database() -> None:
    """Create the commerce database once, including under concurrent startup."""
    RUNTIME.mkdir(exist_ok=True)
    lock_path = RUNTIME / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if DATABASE.exists():
            return
        temporary = RUNTIME / f"commerce.{os.getpid()}.sqlite3"
        connection = sqlite3.connect(temporary)
        try:
            connection.executescript(SEED.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary, DATABASE)


def append_audit(entry: dict) -> None:
    payload = (json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, payload)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def result_digest(result: dict) -> str:
    payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def execute(operation: str, arguments: dict) -> tuple[dict, bool]:
    initialize_database()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        if operation == "profile":
            rows = connection.execute(
                "SELECT preference_key, preference_value "
                "FROM profile_preferences ORDER BY preference_key"
            ).fetchall()
            return (
                {row["preference_key"]: row["preference_value"] for row in rows},
                True,
            )

        if operation == "list":
            rows = connection.execute(
                "SELECT id, name, location, status FROM orders "
                "WHERE location = ? AND status = ? ORDER BY id",
                (arguments["location"], arguments["status"]),
            ).fetchall()
            orders = [row_dict(row) for row in rows]
            return {"count": len(orders), "orders": orders}, True

        if operation == "search":
            rows = connection.execute(
                "SELECT id, name, location, status FROM orders "
                "WHERE name = ? ORDER BY id",
                (arguments["name"],),
            ).fetchall()
            matches = [row_dict(row) for row in rows]
            return {"count": len(matches), "matches": matches}, True

        if operation == "create":
            connection.execute(
                "INSERT INTO orders (id, name, location, status) VALUES (?, ?, ?, ?)",
                (
                    arguments["id"],
                    arguments["name"],
                    arguments["location"],
                    arguments["status"],
                ),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) "
                "VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True

        if operation == "update":
            cursor = connection.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) "
                "VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"updated": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "cancel":
            cursor = connection.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?",
                (arguments["id"],),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) "
                "VALUES (?, ?, 'cancelled')",
                (operation, arguments["id"]),
            )
            connection.commit()
            return {"cancelled": cursor.rowcount == 1, "id": arguments["id"]}, True

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Sandboxed commerce order desk")
    commands = main.add_subparsers(dest="operation", required=True)
    commands.add_parser("profile", help="read saved commerce profile preferences")

    listing = commands.add_parser(
        "list", help="list orders at an exact location with an exact status"
    )
    listing.add_argument("--location", required=True)
    listing.add_argument("--status", required=True)

    search = commands.add_parser("search", help="search orders by exact name")
    search.add_argument("--name", required=True)

    create = commands.add_parser("create", help="create an order")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--status", required=True)

    update = commands.add_parser("update", help="update an order status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = commands.add_parser("cancel", help="cancel an order")
    cancel.add_argument("--id", required=True)
    return main


def main() -> int:
    namespace = parser().parse_args()
    operation = namespace.operation
    arguments = {
        key: value for key, value in vars(namespace).items() if key != "operation"
    }
    started = time.monotonic_ns()
    try:
        result, success = execute(operation, arguments)
    except Exception as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        success = False
    finished = time.monotonic_ns()

    entry = {
        "operation": operation,
        "arguments": arguments,
        "started_ns": started,
        "finished_ns": finished,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "success": success,
        "result_digest": result_digest(result),
    }
    if operation == "list" and success:
        entry["result_ids"] = [order["id"] for order in result["orders"]]
    append_audit(entry)

    stream = sys.stdout if success else sys.stderr
    print(json.dumps(result, indent=2, sort_keys=True), file=stream)
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
