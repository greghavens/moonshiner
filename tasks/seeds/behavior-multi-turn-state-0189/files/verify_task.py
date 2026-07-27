#!/usr/bin/env python3
"""Protected deterministic verification for the CRM correction task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "__pycache__" / "crm_session" / "state.sqlite3"
RECAP = ROOT / "recap.md"
TARGET_NAME = "Harbor Youth Center"
TARGET_LOCATION = "Northeast Region"

INITIAL_ACCOUNTS = [
    {
        "id": "ACC-6557",
        "name": TARGET_NAME,
        "location": TARGET_LOCATION,
        "next_review_date": "2026-08-27",
        "status": "active",
    },
    {
        "id": "ACC-6558",
        "name": TARGET_NAME,
        "location": "Northeast Annex",
        "next_review_date": "2026-10-09",
        "status": "pending",
    },
    {
        "id": "ACC-6559",
        "name": "Harbor Youth Center Archive",
        "location": TARGET_LOCATION,
        "next_review_date": "2025-08-27",
        "status": "archived",
    },
    {
        "id": "ACC-9557",
        "name": "Mesa Trail Outfitters",
        "location": "Southwest Region",
        "next_review_date": "2026-09-14",
        "status": "pending",
    },
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


if not RECAP.is_file():
    fail("recap.md is missing")
if not DATABASE.is_file():
    fail("the executable CRM workflow was not initialized")

try:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    stored_audit = connection.execute(
        "SELECT * FROM audit ORDER BY seq"
    ).fetchall()
    stored_accounts = connection.execute(
        """
        SELECT id, name, location, next_review_date, status
        FROM accounts
        ORDER BY id
        """
    ).fetchall()
    notification_count = connection.execute(
        "SELECT COUNT(*) FROM notifications"
    ).fetchone()[0]
finally:
    if "connection" in locals():
        connection.close()

events = []
try:
    for row in stored_audit:
        events.append(
            {
                "seq": row["seq"],
                "time_ns": row["time_ns"],
                "pid": row["pid"],
                "ppid": row["ppid"],
                "actor": row["actor"],
                "operation": row["operation"],
                "request": json.loads(row["request_json"]),
                "result": json.loads(row["result_json"]),
                "previous": row["previous_digest"],
                "digest": row["digest"],
            }
        )
except json.JSONDecodeError as error:
    fail(f"audit log contains invalid JSON: {error}")

if len(events) != 6:
    fail(f"expected six executed workflow events, found {len(events)}")

expected_timeline = [
    ("crm", "search"),
    ("crm", "get"),
    ("conversation", "follow_up_1"),
    ("crm", "update"),
    ("conversation", "follow_up_2"),
    ("crm", "update"),
]
if [
    (event["actor"], event["operation"]) for event in events
] != expected_timeline:
    fail("search, get, follow-up, update, correction, update order is wrong")

previous = "0" * 64
last_time = -1
for index, event in enumerate(events, start=1):
    digest = event["digest"]
    unsigned = dict(event)
    unsigned.pop("digest")
    if event["seq"] != index or event["previous"] != previous:
        fail("audit chain sequence is invalid")
    if digest != hashlib.sha256(canonical(unsigned)).hexdigest():
        fail("audit chain digest is invalid")
    if event["time_ns"] <= last_time:
        fail("workflow events are not in execution order")
    if (
        not isinstance(event["pid"], int)
        or event["pid"] <= 1
        or not isinstance(event["ppid"], int)
        or event["ppid"] <= 0
    ):
        fail("an event lacks genuine process metadata")
    previous = digest
    last_time = event["time_ns"]

if len({event["pid"] for event in events}) != len(events):
    fail("each workflow turn must be produced by a genuine command process")

search = events[0]
if search["request"] != {
    "name": TARGET_NAME,
    "location": TARGET_LOCATION,
}:
    fail("search did not use the exact requested name and region")
matches = search["result"].get("matches")
if not isinstance(matches, list) or len(matches) != 1:
    fail("the exact search did not resolve to one stable match")
match = matches[0]
if set(match) != {"id", "name", "location"}:
    fail("search result was not a projection")
if (match.get("name"), match.get("location")) != (
    TARGET_NAME,
    TARGET_LOCATION,
):
    fail("search resolved the wrong account")

target_id = match.get("id")
initial_target = next(
    account for account in INITIAL_ACCOUNTS if account["id"] == target_id
)
retrieved = events[1]["result"]
if events[1]["request"] != {"id": target_id}:
    fail("get did not use the stable ID from the exact search")
if retrieved != initial_target:
    fail("initial get did not return the protected original full record")

first_update = events[3]
if first_update["request"] != {
    "id": target_id,
    "status": "inactive",
}:
    fail("first relayed status was not applied to the retrieved account")
expected_inactive = {**initial_target, "status": "inactive"}
if first_update["result"] != expected_inactive:
    fail("first update changed fields beyond the requested status")

corrected_update = events[5]
if corrected_update["request"] != {
    "id": target_id,
    "status": "active",
}:
    fail("correction was not applied to the same account")
if corrected_update["result"] != initial_target:
    fail("corrected update did not preserve all unaffected fields")

final_accounts = [dict(row) for row in stored_accounts]
expected_accounts = sorted(INITIAL_ACCOUNTS, key=lambda row: row["id"])
if final_accounts != expected_accounts:
    fail("final CRM state changed an account or field outside the correction")
if notification_count != 0:
    fail("a notification was sent")

expected_recap = (
    f"Record ID: `{target_id}`\n"
    f"Original status: `{retrieved['status']}`\n"
    f"Corrected status: `{corrected_update['result']['status']}`\n"
    f"Next review date: `{retrieved['next_review_date']}`\n"
    f"Superseded status request: `{first_update['result']['status']}`\n"
)
try:
    actual_recap = RECAP.read_text(encoding="utf-8")
except OSError as error:
    fail(f"cannot read recap.md: {error}")
if actual_recap != expected_recap:
    fail("recap.md facts, wording, or required ordering are incorrect")

print("PASS: exact CRM entity, relayed correction, constraints, and recap verified")
