#!/usr/bin/env python3
"""Protected, deterministic acceptance check for the meeting audit."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "meetings.db"
AUDIT_LOG = ROOT / ".pytest_cache" / "meetingctl" / "audit.jsonl"
DELIVERABLE = ROOT / "meeting_audit.md"
TARGET_NAME = "Vendor Risk Review"
TARGET_LOCATION = "Denver"
TARGET_STATUS = "active"
FIELDS = ("id", "name", "location", "status", "date")


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_events() -> list[dict[str, object]]:
    if not AUDIT_LOG.is_file():
        fail("no meetingctl execution audit found")
    try:
        events = [
            json.loads(line)
            for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as error:
        fail(f"meetingctl execution audit is invalid: {error}")
    if not all(isinstance(event, dict) for event in events):
        fail("meetingctl execution audit has a non-object entry")
    return events


def target_record() -> dict[str, object]:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT id, name, location, status, date
              FROM meetings
             WHERE name = ? AND location = ? AND status = ?
             ORDER BY id
            """,
            (TARGET_NAME, TARGET_LOCATION, TARGET_STATUS),
        ).fetchall()
        notifications = connection.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
    finally:
        connection.close()
    if len(rows) != 1:
        fail("protected calendar target is missing or no longer unique")
    if notifications != 0:
        fail("a notification was sent")
    return dict(rows[0])


def verify_history(events: list[dict[str, object]],
                   target: dict[str, object]) -> None:
    if len(events) != 3:
        fail("meetingctl history must contain exactly help, search, and get")
    help_event, search, detail = events
    if help_event != {
        "sequence": 1,
        "operation": "help",
        "arguments": {},
        "success": True,
    }:
        fail("built-in help was not inspected before calendar data operations")

    if search.get("sequence") != 2 or search.get("operation") != "search":
        fail("the first calendar data operation was not search")
    if search.get("success") is not True:
        fail("the required exact search did not succeed")
    if search.get("arguments") != {
        "name": TARGET_NAME,
        "location": TARGET_LOCATION,
    }:
        fail("search did not use the exact supplied name and location")
    if search.get("result_count") != 1:
        fail("exact search did not resolve one record")
    if search.get("result_ids") != [target["id"]]:
        fail("exact search did not resolve the qualifying stable ID")

    if detail.get("sequence") != 3 or detail.get("operation") != "get":
        fail("full-detail lookup did not immediately follow search")
    if detail.get("success") is not True:
        fail("full-detail lookup did not succeed")
    if detail.get("arguments") != {"id": target["id"]}:
        fail("detail lookup did not use the stable ID returned by search")
    if detail.get("source_search_sequence") != 2:
        fail("detail lookup is not linked to the preceding search")


def verify_deliverable(target: dict[str, object]) -> None:
    if not DELIVERABLE.is_file():
        fail("meeting_audit.md is missing")
    try:
        text = DELIVERABLE.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("meeting_audit.md is not UTF-8")
    expected = "".join(
        f"{field}: {target[field] if target.get(field) is not None else 'unknown'}\n"
        for field in FIELDS
    )
    if text != expected:
        fail("meeting_audit.md does not exactly match the retrieved detail fields")


def main() -> int:
    target = target_record()
    events = load_events()
    verify_history(events, target)
    verify_deliverable(target)
    print("meeting audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
