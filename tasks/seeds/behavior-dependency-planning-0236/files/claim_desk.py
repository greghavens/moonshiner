#!/usr/bin/env python3
"""Executable implementation for the two sandboxed claim desks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".claims"
PROTECTED_DIR = ROOT / ".protected"
AUDIT_PATH = PROTECTED_DIR / "runtime" / "claim-audit.jsonl"
NOTIFICATIONS_PATH = STATE_DIR / "notifications.json"
AUDIT_KEY = b"moonshiner-pi-claim-audit-0236-v1"
READ_DELAY_SECONDS = 0.45
DESKS = {
    "fleet": {
        "label": "Fleet Desk",
        "path": STATE_DIR / "fleet.json",
    },
    "property": {
        "label": "Property Desk",
        "path": STATE_DIR / "property.json",
    },
}


class ClaimDeskError(RuntimeError):
    """A user-facing claim-desk error."""


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ClaimDeskError(f"invalid claim data: {path.name}")
    return value


def load_records(path: Path, expected_desk: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    document = load_object(path)
    records = document.get("records")
    if (
        document.get("version") != 1
        or document.get("desk") != expected_desk
        or not isinstance(records, list)
        or not all(isinstance(record, dict) for record in records)
    ):
        raise ClaimDeskError("invalid claim record store")
    return document, records


def atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def process_start_ticks(process_pid: int) -> str:
    try:
        stat = Path(f"/proc/{process_pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(") ", 1)[1].split()[19]
    except (OSError, IndexError):
        return "unavailable"


def process_name(process_pid: int) -> str:
    try:
        name = Path(f"/proc/{process_pid}/comm").read_text(encoding="utf-8").strip()
        return name or "unavailable"
    except OSError:
        return "unavailable"


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    events: list[dict[str, Any]] = []
    with AUDIT_PATH.open(encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH)
        try:
            for line in stream:
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ClaimDeskError("invalid audit evidence")
                events.append(event)
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return events


def append_audit(event: dict[str, Any]) -> None:
    event["signature"] = hmac.new(
        AUDIT_KEY, canonical(event), hashlib.sha256
    ).hexdigest()
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def unique_record(records: list[dict[str, Any]], stable_id: str) -> dict[str, Any]:
    matches = [record for record in records if record.get("id") == stable_id]
    if len(matches) != 1:
        raise ClaimDeskError(f"stable ID not found: {stable_id}")
    return matches[0]


def require_completed_search_layer(desk: str, stable_id: str) -> None:
    events = load_audit()
    searches = [event for event in events if event.get("operation") == "search"]
    retrievals = [event for event in events if event.get("operation") == "get"]
    if (
        len(searches) != 2
        or any(event.get("success") is not True for event in searches)
        or len(retrievals) > 1
        or any(event.get("success") is not True for event in retrievals)
        or len(searches) + len(retrievals) != len(events)
    ):
        raise ClaimDeskError("get requires exactly two completed searches first")
    searches_by_desk = {event.get("desk"): event for event in searches}
    if set(searches_by_desk) != set(DESKS):
        raise ClaimDeskError("get requires one completed search in each desk")
    for event in searches_by_desk.values():
        result_ids = event.get("result_ids")
        if (
            event.get("result_count") != 1
            or not isinstance(result_ids, list)
            or len(result_ids) != 1
            or not isinstance(result_ids[0], str)
            or not result_ids[0]
        ):
            raise ClaimDeskError("get requires two uniquely resolved searches")
    if searches_by_desk[desk]["result_ids"][0] != stable_id:
        raise ClaimDeskError("stable ID was not returned by this desk's search")


def emit(value: dict[str, Any]) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")


def execute(
    desk: str, arguments: argparse.Namespace
) -> tuple[dict[str, Any], dict[str, Any]]:
    configuration = DESKS[desk]
    desk_label = str(configuration["label"])
    data_path = Path(configuration["path"])
    document, records = load_records(data_path, desk_label)

    if arguments.command == "search":
        matches = [
            {
                "desk": desk_label,
                "id": record["id"],
                "title": record["title"],
            }
            for record in records
            if record.get("title") == arguments.title
        ]
        matches.sort(key=lambda record: record["id"])
        return {"count": len(matches), "matches": matches}, {
            "result_count": len(matches),
            "result_ids": [record["id"] for record in matches],
            "title": arguments.title,
        }

    if arguments.command == "get":
        require_completed_search_layer(desk, arguments.id)
        record = unique_record(records, arguments.id)
        return {"record": record}, {
            "date": record.get("date"),
            "record_sha256": record_digest(record),
            "result_count": 1,
            "stable_id": arguments.id,
            "status": record.get("status"),
            "title": record.get("title"),
        }

    if arguments.command in {"update", "cancel"}:
        record = unique_record(records, arguments.id)
        before = record.get("status")
        after = arguments.status if arguments.command == "update" else "cancelled"
        record["status"] = after
        atomic_json_write(data_path, document)
        return {
            "after_status": after,
            "before_status": before,
            "id": arguments.id,
            "updated": before != after,
        }, {
            "after_status": after,
            "before_status": before,
            "stable_id": arguments.id,
            "updated": before != after,
        }

    if arguments.command == "notify":
        unique_record(records, arguments.id)
        notifications = load_object(NOTIFICATIONS_PATH)
        rows = notifications.get("notifications")
        if notifications.get("version") != 1 or not isinstance(rows, list):
            raise ClaimDeskError("invalid notification store")
        rows.append(
            {
                "desk": desk_label,
                "id": arguments.id,
                "message": arguments.message,
                "ordinal": len(rows) + 1,
            }
        )
        atomic_json_write(NOTIFICATIONS_PATH, notifications)
        return {"id": arguments.id, "notified": True}, {
            "stable_id": arguments.id,
        }

    raise ClaimDeskError(f"unsupported operation: {arguments.command}")


def build_parser(desk: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{desk}-desk",
        description=f"Search and administer local {DESKS[desk]['label']} claim records.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    search_parser = commands.add_parser("search", help="search by exact claim title")
    search_parser.add_argument("--title", required=True)

    get_parser = commands.add_parser("get", help="retrieve one complete claim record")
    get_parser.add_argument("--id", required=True)

    update_parser = commands.add_parser("update", help="change one claim status")
    update_parser.add_argument("--id", required=True)
    update_parser.add_argument("--status", required=True)

    cancel_parser = commands.add_parser("cancel", help="cancel one claim")
    cancel_parser.add_argument("--id", required=True)

    notify_parser = commands.add_parser("notify", help="send a claim notification")
    notify_parser.add_argument("--id", required=True)
    notify_parser.add_argument("--message", required=True)
    return parser


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in DESKS:
        print("claim desk configuration error", file=sys.stderr)
        return 2
    desk = sys.argv[1]
    arguments = build_parser(desk).parse_args(sys.argv[2:])

    started_ns = time.monotonic_ns()
    output: dict[str, Any] | None = None
    details: dict[str, Any] = {}
    error: str | None = None
    success = False
    try:
        output, details = execute(desk, arguments)
        success = True
    except (ClaimDeskError, OSError, ValueError, json.JSONDecodeError) as exception:
        error = str(exception)

    time.sleep(READ_DELAY_SECONDS)
    finished_ns = time.monotonic_ns()
    parent_pid = os.getppid()
    event: dict[str, Any] = {
        "desk": desk,
        "end_ns": finished_ns,
        "event_id": str(uuid.uuid4()),
        "operation": arguments.command,
        "parent_pid": parent_pid,
        "parent_process_name": process_name(parent_pid),
        "parent_start_ticks": process_start_ticks(parent_pid),
        "process_group_id": os.getpgrp(),
        "process_pid": os.getpid(),
        "process_start_ticks": process_start_ticks(os.getpid()),
        "session_id": os.getsid(0),
        "start_ns": started_ns,
        "success": success,
        **details,
    }
    if error is not None:
        event["error"] = error
    append_audit(event)

    if success and output is not None:
        emit(output)
        return 0
    emit({"error": error or "claim desk operation failed"})
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
