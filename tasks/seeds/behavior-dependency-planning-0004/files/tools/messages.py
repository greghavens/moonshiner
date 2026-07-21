#!/usr/bin/env python3
"""Executable local outbound-message service for the audit exercise."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MESSAGES_PATH = DATA_DIR / "messages.json"
PREFERENCES_PATH = DATA_DIR / "preferences.json"
NOTIFICATIONS_PATH = DATA_DIR / "notifications.json"
TRACE_PATH = ROOT / "evidence" / "audit_trace.jsonl"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def append_trace(payload: dict[str, Any]) -> None:
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRACE_PATH.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        handle.flush()
        fcntl.flock(handle, fcntl.LOCK_UN)


def result_for(args: argparse.Namespace) -> dict[str, Any]:
    messages = load_json(MESSAGES_PATH)

    if args.action == "search":
        matches = [
            {"id": row["id"], "name": row["name"], "location": row["location"]}
            for row in messages
            if row["name"] == args.name and row["location"] == args.location
        ]
        return {
            "operation": "search",
            "arguments": {"name": args.name, "location": args.location},
            "result": {"matches": matches},
        }

    if args.action == "get":
        record = next((row for row in messages if row["id"] == args.id), None)
        return {
            "operation": "get",
            "arguments": {"id": args.id},
            "result": {"record": record},
        }

    if args.action == "list":
        rows = messages
        if args.location is not None:
            rows = [row for row in rows if row["location"] == args.location]
        return {
            "operation": "list",
            "arguments": {"location": args.location},
            "result": {"records": rows},
        }

    if args.action == "preferences":
        return {
            "operation": "preferences",
            "arguments": {},
            "result": load_json(PREFERENCES_PATH),
        }

    if args.action == "availability":
        conflicts = [
            row["id"]
            for row in messages
            if row["location"] == args.location and row["status"] == "scheduled"
        ]
        return {
            "operation": "availability",
            "arguments": {"location": args.location},
            "result": {"available": not conflicts, "conflicting_ids": conflicts},
        }

    if args.action == "create":
        if any(row["id"] == args.id for row in messages):
            outcome = {"created": False, "reason": "stable ID already exists"}
        else:
            record = {
                "id": args.id,
                "name": args.name,
                "location": args.location,
                "status": args.status,
                "scheduled_for": None,
                "channel": "email",
                "audience_size": 0,
                "owner": "Unassigned",
                "subject": args.name,
            }
            messages.append(record)
            save_json(MESSAGES_PATH, messages)
            outcome = {"created": True, "record": record}
        return {
            "operation": "create",
            "arguments": {
                "id": args.id,
                "name": args.name,
                "location": args.location,
                "status": args.status,
            },
            "result": outcome,
        }

    if args.action in {"update", "cancel"}:
        record = next((row for row in messages if row["id"] == args.id), None)
        if record is None:
            outcome = {"changed": False, "reason": "stable ID not found"}
        else:
            record["status"] = "canceled" if args.action == "cancel" else args.status
            save_json(MESSAGES_PATH, messages)
            outcome = {"changed": True, "record": record}
        arguments = {"id": args.id}
        if args.action == "update":
            arguments["status"] = args.status
        return {"operation": args.action, "arguments": arguments, "result": outcome}

    if args.action == "notify":
        notifications = load_json(NOTIFICATIONS_PATH)
        notification = {"id": args.id, "message": args.message}
        notifications.append(notification)
        save_json(NOTIFICATIONS_PATH, notifications)
        return {
            "operation": "notify",
            "arguments": notification,
            "result": {"sent": True},
        }

    raise AssertionError(f"unhandled action: {args.action}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        description="Query and maintain the sandboxed outbound-message collection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "read-only query syntax:\n"
            "  python3 tools/messages.py search --name NAME --location LOCATION\n"
            "  python3 tools/messages.py get --id ID\n"
            "Run an action with --help for its complete option details."
        ),
    )
    actions = root.add_subparsers(dest="action", required=True)

    search = actions.add_parser("search", help="Search by exact name and location.")
    search.add_argument("--name", required=True)
    search.add_argument("--location", required=True)

    get = actions.add_parser("get", help="Retrieve one full record by stable ID.")
    get.add_argument("--id", required=True)

    listing = actions.add_parser("list", help="List collection records.")
    listing.add_argument("--location")

    actions.add_parser("preferences", help="Read saved service preferences.")

    availability = actions.add_parser("availability", help="Check a location queue.")
    availability.add_argument("--location", required=True)

    create = actions.add_parser("create", help="Create a message record.")
    create.add_argument("--id", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--location", required=True)
    create.add_argument("--status", default="draft")

    update = actions.add_parser("update", help="Update a message status.")
    update.add_argument("--id", required=True)
    update.add_argument("--status", required=True)

    cancel = actions.add_parser("cancel", help="Cancel a message record.")
    cancel.add_argument("--id", required=True)

    notify = actions.add_parser("notify", help="Send a message notification.")
    notify.add_argument("--id", required=True)
    notify.add_argument("--message", required=True)
    return root


def main() -> int:
    args = parser().parse_args()
    payload = result_for(args)
    capture_path = os.environ.get("COMM_AUDIT_CAPTURE")
    if capture_path:
        Path(capture_path).write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    else:
        append_trace({"batch": None, "parallel": False, "commands": [payload]})
    print(json.dumps(payload["result"], indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
