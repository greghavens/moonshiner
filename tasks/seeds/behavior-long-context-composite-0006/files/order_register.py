#!/usr/bin/env python3
"""Genuine SQLite-backed command-line order register for the sandbox."""

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
RECORD_FIELDS = (
    "id, name, location, status, order_date, account, item_count, total_cents"
)


def initialize_database() -> None:
    """Materialize the SQLite service state once with a process-safe lock."""
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME / "initialize.lock").open("a+b") as lock:
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
    payload = (
        json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    descriptor = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.write(descriptor, payload)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def row_dict(row: sqlite3.Row) -> dict:
    return {key: row[key] for key in row.keys()}


def digest(value: dict) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def execute(operation: str, arguments: dict) -> tuple[dict, bool, dict]:
    initialize_database()
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    evidence: dict = {}
    try:
        if operation == "search":
            rows = connection.execute(
                "SELECT id, name, location FROM orders "
                "WHERE name = ? AND location = ? ORDER BY id",
                (arguments["name"], arguments["location"]),
            ).fetchall()
            matches = [row_dict(row) for row in rows]
            evidence["result_ids"] = [match["id"] for match in matches]
            return {"count": len(matches), "matches": matches}, True, evidence

        if operation == "get":
            row = connection.execute(
                f"SELECT {RECORD_FIELDS} FROM orders WHERE id = ?",
                (arguments["id"],),
            ).fetchone()
            if row is None:
                return (
                    {"error": "order not found", "id": arguments["id"]},
                    False,
                    evidence,
                )
            record = row_dict(row)
            evidence["record_digest"] = digest(record)
            return record, True, evidence

        if operation == "list":
            rows = connection.execute(
                "SELECT id, name, location FROM orders ORDER BY id"
            ).fetchall()
            return {"orders": [row_dict(row) for row in rows]}, True, evidence

        if operation == "profile":
            rows = connection.execute(
                "SELECT profile_key, profile_value FROM profiles ORDER BY profile_key"
            ).fetchall()
            return (
                {row["profile_key"]: row["profile_value"] for row in rows},
                True,
                evidence,
            )

        if operation == "availability":
            row = connection.execute(
                "SELECT sku, location, units FROM availability "
                "WHERE sku = ? AND location = ?",
                (arguments["sku"], arguments["location"]),
            ).fetchone()
            result = (
                {"available": False}
                if row is None
                else {"available": row["units"] > 0, **row_dict(row)}
            )
            return result, True, evidence

        if operation == "create":
            connection.execute(
                "INSERT INTO orders "
                "(id, name, location, status, order_date, account, item_count, total_cents) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    arguments["id"],
                    arguments["name"],
                    arguments["location"],
                    arguments["status"],
                    arguments["order_date"],
                    arguments["account"],
                    arguments["item_count"],
                    arguments["total_cents"],
                ),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return {"created": arguments["id"]}, True, evidence

        if operation == "update":
            cursor = connection.execute(
                "UPDATE orders SET status = ? WHERE id = ?",
                (arguments["status"], arguments["id"]),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], arguments["status"]),
            )
            connection.commit()
            return (
                {"updated": cursor.rowcount == 1, "id": arguments["id"]},
                True,
                evidence,
            )

        if operation == "cancel":
            cursor = connection.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = ?",
                (arguments["id"],),
            )
            connection.execute(
                "INSERT INTO mutation_log (operation, order_id, detail) VALUES (?, ?, ?)",
                (operation, arguments["id"], "cancelled"),
            )
            connection.commit()
            return (
                {"cancelled": cursor.rowcount == 1, "id": arguments["id"]},
                True,
                evidence,
            )

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
            return {"notified": arguments["id"]}, True, evidence

        raise ValueError(f"unsupported operation: {operation}")
    finally:
        connection.close()


def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description="Sandboxed commerce order register")
    commands = main.add_subparsers(dest="operation", required=True)

    search = commands.add_parser("search", help="search by exact name and location")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one full order by stable ID")
    get.add_argument("--id", required=True)

    commands.add_parser("list", help="list order summaries")
    commands.add_parser("profile", help="read the saved commerce profile")

    availability = commands.add_parser("availability", help="check item availability")
    availability.add_argument("--sku", required=True)
    availability.add_argument("--location", required=True)

    create = commands.add_parser("create", help="create an order")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--status", required=True)
    create.add_argument("--order-date", required=True)
    create.add_argument("--account", required=True)
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
    started_ns = time.monotonic_ns()
    success = False
    evidence: dict = {}
    try:
        result, success, evidence = execute(operation, arguments)
    except (sqlite3.Error, OSError, ValueError) as error:
        result = {"error": str(error)}
    finished_ns = time.monotonic_ns()
    append_audit(
        {
            "operation": operation,
            "arguments": arguments,
            "success": success,
            "started_ns": started_ns,
            "finished_ns": finished_ns,
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            **evidence,
        }
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
