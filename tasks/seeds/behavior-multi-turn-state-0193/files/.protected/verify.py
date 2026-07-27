#!/usr/bin/env python3
"""Protected deterministic verification for the library correction task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".library-runtime" / "catalog.sqlite3"
HELP_MARKER = ROOT / ".library-runtime" / "help-viewed.json"
REPORT = ROOT / "library-report.txt"
TARGET_TITLE = "Orchard Birds"
TARGET_BRANCH = "North Branch"

INITIAL_RECORDS = [
    {
        "id": "TTL-6609",
        "title": TARGET_TITLE,
        "branch": TARGET_BRANCH,
        "hold_until": None,
        "status": "active",
    },
    {
        "id": "TTL-6610",
        "title": TARGET_TITLE,
        "branch": "South Branch",
        "hold_until": "2026-08-21",
        "status": "on-hold",
    },
    {
        "id": "TTL-6611",
        "title": "Orchard Birds: Field Notes",
        "branch": TARGET_BRANCH,
        "hold_until": "2026-09-04",
        "status": "available",
    },
    {
        "id": "TTL-9609",
        "title": "Lanterns at Noon",
        "branch": "West Branch",
        "hold_until": "2026-09-18",
        "status": "pending",
    },
]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


if not REPORT.is_file():
    fail("library-report.txt is missing")
if not DATABASE.is_file():
    fail("the executable library workflow was not initialized")
if not HELP_MARKER.is_file():
    fail("top-level libraryctl help was not viewed")

try:
    help_event = json.loads(HELP_MARKER.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    fail(f"libraryctl help evidence is invalid: {error}")
if (
    help_event.get("argv") != ["--help"]
    or not isinstance(help_event.get("time_ns"), int)
    or not isinstance(help_event.get("pid"), int)
    or help_event["pid"] <= 1
    or not isinstance(help_event.get("ppid"), int)
    or help_event["ppid"] <= 0
):
    fail("libraryctl help evidence lacks genuine process metadata")

try:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    stored_audit = connection.execute(
        "SELECT * FROM audit ORDER BY seq"
    ).fetchall()
    stored_records = connection.execute(
        """
        SELECT id, title, branch, hold_until, status
        FROM titles
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
                "operation": row["operation"],
                "request": json.loads(row["request_json"]),
                "result": json.loads(row["result_json"]),
                "previous": row["previous_digest"],
                "digest": row["digest"],
            }
        )
except json.JSONDecodeError as error:
    fail(f"audit log contains invalid JSON: {error}")

if len(events) != 4:
    fail(f"expected four executed catalog operations, found {len(events)}")

if [event["operation"] for event in events] != [
    "search",
    "get",
    "update-status",
    "update-status",
]:
    fail("exact search, dependent get, first update, and correction order is wrong")

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
        fail("catalog operations are not in execution order")
    if (
        not isinstance(event["pid"], int)
        or event["pid"] <= 1
        or not isinstance(event["ppid"], int)
        or event["ppid"] <= 0
    ):
        fail("an operation lacks genuine process metadata")
    previous = digest
    last_time = event["time_ns"]

if len({event["pid"] for event in events}) != len(events):
    fail("each catalog operation must be produced by a separate command process")
if (
    help_event["time_ns"] >= events[0]["time_ns"]
    or help_event["pid"] in {event["pid"] for event in events}
):
    fail("top-level libraryctl help was not viewed before the exact search")

search = events[0]
if search["request"] != {
    "title": TARGET_TITLE,
    "branch": TARGET_BRANCH,
}:
    fail("search did not use the exact requested title and branch")
matches = search["result"].get("matches")
if not isinstance(matches, list) or len(matches) != 1:
    fail("the exact search did not resolve to one stable match")
match = matches[0]
if set(match) != {"id", "title", "branch"}:
    fail("search result was not a projection")
if (match.get("title"), match.get("branch")) != (
    TARGET_TITLE,
    TARGET_BRANCH,
):
    fail("search resolved the wrong title")

target_id = match.get("id")
if not isinstance(target_id, str) or not target_id:
    fail("search did not return a nonempty stable ID")
initial_target = next(
    (record for record in INITIAL_RECORDS if record["id"] == target_id), None
)
if initial_target is None:
    fail("search returned an unrecognized record")

retrieved = events[1]["result"]
if events[1]["request"] != {"id": target_id}:
    fail("get did not use the stable ID from the exact search")
if retrieved != initial_target:
    fail("get did not return the protected original full record")

first_update = events[2]
if first_update["request"] != {
    "id": target_id,
    "status": "lost",
}:
    fail("first status instruction was not applied to the retrieved title")
expected_lost = {**initial_target, "status": "lost"}
if first_update["result"] != expected_lost:
    fail("first update changed a field beyond the requested status")

corrected_update = events[3]
if corrected_update["request"] != {
    "id": target_id,
    "status": "on-hold",
}:
    fail("correction was not applied to the same title")
expected_corrected = {**initial_target, "status": "on-hold"}
if corrected_update["result"] != expected_corrected:
    fail("corrected update did not preserve all unaffected fields")

final_records = [dict(row) for row in stored_records]
expected_records = [
    (
        expected_corrected
        if record["id"] == target_id
        else record
    )
    for record in sorted(INITIAL_RECORDS, key=lambda item: item["id"])
]
if final_records != expected_records:
    fail("final catalog state changed an unrelated record or field")
if notification_count != 0:
    fail("a notification was sent")

hold_until = retrieved.get("hold_until")
reported_hold = (
    "unknown"
    if hold_until is None or str(hold_until).strip() == ""
    else str(hold_until)
)
expected_report = (
    f"ID: {target_id}\n"
    f"Hold-until date: {reported_hold}\n"
    f"Status: {corrected_update['result']['status']}\n"
)
try:
    actual_report = REPORT.read_text(encoding="utf-8")
except OSError as error:
    fail(f"cannot read library-report.txt: {error}")
if actual_report != expected_report:
    fail("library-report.txt facts, unknown handling, or format are incorrect")

print(
    "PASS: exact title, dependent retrieval, superseded update, "
    "correction, and negative constraints verified"
)
