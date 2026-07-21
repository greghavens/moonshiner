#!/usr/bin/env python3
"""Executable, SQLite-backed procurement sandbox for the order audit."""

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
RUNTIME = ROOT / ".order-runtime"
DATABASE = RUNTIME / "orders.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
SEED = ROOT / "order_seed.sql"


def initialize_database() -> None:
    """Materialize the service database once, safely under concurrent startup."""
    RUNTIME.mkdir(exist_ok=True)
    lock_path = RUNTIME / "initialize.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if DATABASE.exists():
            return
        temporary = RUNTIME / f"orders.{os.getpid()}.sqlite3"
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


def record_digest(record: dict) -> str:
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def execute(operation: str, arguments: dict) -> tuple[dict, bool]:
    initialize_database()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    try:
        if operation == "search":
            rows = connection.execute(
                "SELECT id, name, location FROM purchase_orders "
                "WHERE name = ? AND location = ? ORDER BY id",
                (arguments["name"], arguments["location"]),
            ).fetchall()
            matches = [row_dict(row) for row in rows]
            return {"count": len(matches), "matches": matches}, True

        if operation == "get":
            row = connection.execute(
                "SELECT id, name, location, status, requested_for, vendor, "
                "item_count, total_cents FROM purchase_orders WHERE id = ?",
                (arguments["id"],),
            ).fetchone()
            if row is None:
                return {"error": "order not found", "id": arguments["id"]}, False
            return row_dict(row), True

        if operation == "list":
            rows = connection.execute(
                "SELECT id, name, location FROM purchase_orders ORDER BY id"
            ).fetchall()
            return {"orders": [row_dict(row) for row in rows]}, True

        if operation == "preferences":
            rows = connection.execute(
                "SELECT preference_key, preference_value FROM preferences "
                "ORDER BY preference_key"
            ).fetchall()
            return {row["preference_key"]: row["preference_value"] for row in rows}, True

        if operation == "availability":
            row = connection.execute(
                "SELECT sku, location, units FROM availability "
                "WHERE sku = ? AND location = ?",
                (arguments["sku"], arguments["location"]),
            ).fetchone()
            return (
                {"available": False}
                if row is None
                else {"available": row["units"] > 0, **row_dict(row)}
            ), True

        if operation == "create":
            connection.execute(
                "INSERT INTO purchase_orders "
                "(id, name, location, status, requested_for, vendor, item_count, total_cents) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    arguments["id"],
                    arguments["name"],
                    arguments["location"],
                    arguments["status"],
                    arguments["requested_for"],
                    arguments["vendor"],
                    arguments["item_count"],
                    arguments["total_cents"],
                ),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True

        if operation == "update":
            cursor = connection.execute(
                "UPDATE purchase_orders SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"updated": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "cancel":
            cursor = connection.execute(
                "UPDATE purchase_orders SET status = 'cancelled' WHERE id = ?",
                (arguments["id"],),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], "cancelled"),
            )
            connection.commit()
            return {"cancelled": cursor.rowcount == 1, "id": arguments["id"]}, True

        if operation == "notify":
            connection.execute(
                "INSERT INTO notifications (order_id, message) VALUES (?, ?)",
                (arguments["id"], arguments["message"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["message"]),
            )
            connection.commit()
            return {"notified": arguments["id"]}, True

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Sandboxed procurement order desk")
    commands = main.add_subparsers(dest="operation", required=True)

    search = commands.add_parser("search", help="search by exact name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one full order by stable ID")
    get.add_argument("--id", required=True)

    commands.add_parser("list", help="list order summaries")
    commands.add_parser("preferences", help="read saved procurement preferences")

    availability = commands.add_parser("availability", help="check stock availability")
    availability.add_argument("--sku", required=True)
    availability.add_argument("--location", required=True)

    create = commands.add_parser("create", help="create an order")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--requested-for", required=True)
    create.add_argument("--vendor", required=True)
    create.add_argument("--item-count", type=int, required=True)
    create.add_argument("--total-cents", type=int, required=True)

    update = commands.add_parser("update", help="update an order status")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = commands.add_parser("cancel", help="cancel an order")
    cancel.add_argument("--id", required=True)

    notify = commands.add_parser("notify", help="send an order notification")
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
    try:
        result, success = execute(operation, arguments)
        if operation in {"search", "get"}:
            time.sleep(0.25)
    except Exception as error:
        result = {"error": f"{type(error).__name__}: {error}"}
        success = False
    finished = time.monotonic_ns()

    audit = {
        "operation": operation,
        "arguments": arguments,
        "started_ns": started,
        "finished_ns": finished,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "success": success,
    }
    if operation == "search" and success:
        audit["result_ids"] = [match["id"] for match in result["matches"]]
    if operation == "get" and success:
        audit["record_digest"] = record_digest(result)
    append_audit(audit)
    stream = sys.stdout if success else sys.stderr
    print(json.dumps(result, indent=2, sort_keys=True), file=stream)
    return 0 if success else 3


if __name__ == "__main__":
    raise SystemExit(main())
