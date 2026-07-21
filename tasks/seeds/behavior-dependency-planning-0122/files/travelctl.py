#!/usr/bin/env python3
"""Small file-backed Travel Desk used by the reconciliation exercise."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / ".travel"
RECORDS = DATA_DIR / "records.json"
NOTIFICATIONS = DATA_DIR / "notifications.json"
AUDIT = DATA_DIR / "audit.jsonl"
READ_DELAY_SECONDS = 0.35


def audit(entry: dict[str, Any]) -> None:
    """Append one complete JSON event while cooperating with concurrent calls."""
    DATA_DIR.mkdir(exist_ok=True)
    with AUDIT.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def load_records() -> list[dict[str, str]]:
    with RECORDS.open(encoding="utf-8") as stream:
        return json.load(stream)


def begin(operation: str, arguments: dict[str, str]) -> tuple[str, int]:
    operation_id = secrets.token_hex(8)
    started = time.monotonic_ns()
    audit({
        "arguments": arguments,
        "event": "start",
        "operation": operation,
        "operation_id": operation_id,
        "time_ns": started,
    })
    return operation_id, started


def finish(operation: str, operation_id: str, result: dict[str, Any]) -> None:
    audit({
        "event": "finish",
        "operation": operation,
        "operation_id": operation_id,
        "result": result,
        "time_ns": time.monotonic_ns(),
    })


def fail(operation: str, operation_id: str, message: str) -> None:
    finish(operation, operation_id, {"error": message})
    print(json.dumps({"error": message}, sort_keys=True), file=sys.stderr)
    raise SystemExit(1)


def do_search(query: str, location: str) -> None:
    arguments = {"location": location, "query": query}
    operation_id, _ = begin("search", arguments)
    time.sleep(READ_DELAY_SECONDS)
    folded_query = query.casefold()
    folded_location = location.casefold()
    matches = [
        {key: record[key] for key in ("id", "location", "name")}
        for record in load_records()
        if folded_query in record["name"].casefold()
        and record["location"].casefold() == folded_location
    ]
    result: dict[str, Any] = {"matches": matches}
    finish("search", operation_id, result)
    print(json.dumps(result, sort_keys=True))


def do_get(record_id: str) -> None:
    arguments = {"id": record_id}
    operation_id, _ = begin("get", arguments)
    time.sleep(READ_DELAY_SECONDS)
    match = next((record for record in load_records() if record["id"] == record_id), None)
    if match is None:
        fail("get", operation_id, f"record not found: {record_id}")
    result = {"record": match}
    finish("get", operation_id, result)
    print(json.dumps(result, sort_keys=True))


def do_update(record_id: str, status: str) -> None:
    arguments = {"id": record_id, "status": status}
    operation_id, _ = begin("update", arguments)
    with RECORDS.open("r+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        records = json.load(stream)
        match = next((record for record in records if record["id"] == record_id), None)
        if match is None:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            fail("update", operation_id, f"record not found: {record_id}")
        previous_status = match["status"]
        match["status"] = status
        stream.seek(0)
        json.dump(records, stream, indent=2)
        stream.write("\n")
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    result = {
        "id": record_id,
        "previous_status": previous_status,
        "status": status,
    }
    finish("update", operation_id, result)
    print(json.dumps(result, sort_keys=True))


def do_notify(record_id: str, message: str) -> None:
    arguments = {"id": record_id, "message": message}
    operation_id, _ = begin("notify", arguments)
    with NOTIFICATIONS.open("r+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        notifications = json.load(stream)
        notifications.append({"id": record_id, "message": message})
        stream.seek(0)
        json.dump(notifications, stream, indent=2)
        stream.write("\n")
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    result = {"delivered": True, "id": record_id}
    finish("notify", operation_id, result)
    print(json.dumps(result, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="operate the local Travel Desk")
    commands = root.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search", help="find records by name and location")
    search.add_argument("--query", required=True)
    search.add_argument("--location", required=True)

    get = commands.add_parser("get", help="retrieve one record")
    get.add_argument("record_id")

    update = commands.add_parser("update", help="change one record status")
    update.add_argument("record_id")
    update.add_argument("--status", required=True)

    notify = commands.add_parser("notify", help="send a record notification")
    notify.add_argument("record_id")
    notify.add_argument("--message", required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "search":
        do_search(args.query, args.location)
    elif args.command == "get":
        do_get(args.record_id)
    elif args.command == "update":
        do_update(args.record_id, args.status)
    elif args.command == "notify":
        do_notify(args.record_id, args.message)


if __name__ == "__main__":
    main()
