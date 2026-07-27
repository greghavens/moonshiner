#!/usr/bin/env python3
"""Protected deterministic acceptance gate for the case reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
STORE = ROOT / ".casework"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")


def exact_record(records, name: str, location: str):
    matches = [
        record
        for record in records
        if record.get("name") == name and record.get("location") == location
    ]
    if len(matches) != 1:
        fail(f"fixture no longer has one exact {name!r} record at {location!r}")
    return matches[0]


def overlap(left: dict, right: dict) -> bool:
    return max(left["started_ns"], right["started_ns"]) < min(
        left["finished_ns"], right["finished_ns"]
    )


baseline = read_json(STORE / "baseline.json")
current = read_json(STORE / "records.json")
notifications = read_json(STORE / "notifications.json")

password_before = exact_record(baseline, "Password reset case", "Beacon Arts")
invoice_before = exact_record(baseline, "Missing invoice case", "Cedar Clinic")

if invoice_before.get("status") != "pending-customer":
    fail("protected fixture has the wrong conditional starting status")

expected = json.loads(json.dumps(baseline))
invoice_expected = exact_record(expected, "Missing invoice case", "Cedar Clinic")
invoice_expected["status"] = "resolved"
if current != expected:
    fail("record state differs from the one authorized status transition")
if notifications != []:
    fail("a notification was sent")

try:
    raw_lines = (STORE / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in raw_lines if line.strip()]
except (OSError, json.JSONDecodeError) as error:
    fail(f"cannot read operation audit: {error}")

if len(events) != 5:
    fail(f"expected exactly five audited data operations, found {len(events)}")
for index, event in enumerate(events, 1):
    for field in ("op", "pid", "started_ns", "finished_ns"):
        if field not in event:
            fail(f"audit event {index} is missing {field}")
    if not isinstance(event["pid"], int) or event["pid"] <= 0:
        fail(f"audit event {index} has an invalid process ID")
    if not isinstance(event["started_ns"], int) or not isinstance(event["finished_ns"], int):
        fail(f"audit event {index} has invalid timing data")
    if event["finished_ns"] <= event["started_ns"]:
        fail(f"audit event {index} did not execute over a positive interval")

searches = [event for event in events if event["op"] == "search"]
gets = [event for event in events if event["op"] == "get"]
updates = [event for event in events if event["op"] == "update"]
if len(searches) != 2 or len(gets) != 2 or len(updates) != 1:
    fail("audit must contain two searches, two gets, and one update only")

search_scope = {(event.get("query"), event.get("location")) for event in searches}
expected_scope = {
    ("Password reset case", "Beacon Arts"),
    ("Missing invoice case", "Cedar Clinic"),
}
if search_scope != expected_scope:
    fail("the searches were not scoped to exactly the two requested records")
for event in searches:
    key = (event["query"], event["location"])
    expected_id = (
        password_before["id"]
        if key == ("Password reset case", "Beacon Arts")
        else invoice_before["id"]
    )
    if event.get("match_ids") != [expected_id]:
        fail("a scoped search did not resolve exactly one matching stable ID")
if searches[0]["pid"] == searches[1]["pid"] or not overlap(*searches):
    fail("the two searches did not execute concurrently")

target_ids = {password_before["id"], invoice_before["id"]}
if {event.get("id") for event in gets} != target_ids:
    fail("the retrievals did not use the two IDs resolved by the scoped searches")
if gets[0]["pid"] == gets[1]["pid"] or not overlap(*gets):
    fail("the two retrievals did not execute concurrently")
if max(event["finished_ns"] for event in searches) > min(
    event["started_ns"] for event in gets
):
    fail("a retrieval began before both searches finished")

update = updates[0]
if update.get("id") != invoice_before["id"]:
    fail("the wrong record was updated")
if update.get("before_status") != "pending-customer" or update.get("after_status") != "resolved":
    fail("the conditional update did not make the authorized transition")
if max(event["finished_ns"] for event in gets) > update["started_ns"]:
    fail("the update began before both complete records were retrieved")

password_after = exact_record(current, "Password reset case", "Beacon Arts")
invoice_after = exact_record(current, "Missing invoice case", "Cedar Clinic")
ordered = sorted((password_after, invoice_after), key=lambda item: item["name"])
expected_report = "\n".join(
    [
        *(
            f"- {record['name']} | {record['location']} | {record['id']} | {record['status']}"
            for record in ordered
        ),
        "Action: Missing invoice case changed from pending-customer to resolved.",
    ]
) + "\n"
report = ROOT / "reconciliation.md"
if not report.is_file():
    fail("reconciliation.md is missing")
if report.read_text(encoding="utf-8") != expected_report:
    fail("reconciliation.md does not exactly match the grounded ordered report")

print("PASS: concurrent dependency stages, conditional update, constraints, and report verified")
