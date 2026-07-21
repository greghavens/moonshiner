#!/usr/bin/env python3
"""Protected acceptance checks for the order reconciliation trajectory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "orders.db"
AUDIT_LOG = ROOT / ".orderdesk" / "audit.jsonl"
REPORT = ROOT / "audit_report.txt"
DATABASE_SHA256 = "31ba0826c1780d687ffc07709fc07406866901a403d03a6ecd94d08e39f20bed"

BRANCHES = [
    ("Clinic Linen Order", "Cedar Clinic", "com-146", "processing"),
    ("Mobile Lab Cooler Order", "Dale Clinic", "com-546", "backordered"),
]
EXPECTED_REPORT = (
    "Clinic Linen Order at Cedar Clinic is processing.\n"
    "Mobile Lab Cooler Order at Dale Clinic is backordered.\n"
    "Statuses differ: processing versus backordered.\n"
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_events() -> list[dict]:
    if not AUDIT_LOG.is_file():
        fail("no orderdesk audit log; execute the required order operations")
    events = []
    for number, line in enumerate(AUDIT_LOG.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"audit log line {number} is invalid JSON: {error}")
        if not isinstance(event, dict):
            fail(f"audit log line {number} is not an event")
        events.append(event)
    return events


def verify_database() -> None:
    digest = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    if digest != DATABASE_SHA256:
        fail("the protected order database changed during this read-only audit")
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
        notifications = connection.execute(
            "SELECT count(*) FROM notifications"
        ).fetchone()[0]
        mutations = connection.execute(
            "SELECT count(*) FROM mutation_log"
        ).fetchone()[0]
    if notifications or mutations:
        fail("notification or mutation state changed")


def verify_search(event: dict) -> dict[tuple[str, str], str]:
    if event.get("sequence") != 1 or event.get("operation") != "search-batch":
        fail("the first data operation must be one batched search")
    requests = event.get("requests")
    results = event.get("results")
    wanted = {(name, location) for name, location, _, _ in BRANCHES}
    observed = {
        (request.get("name"), request.get("location"))
        for request in requests or [] if isinstance(request, dict)
    }
    if len(requests or []) != 2 or observed != wanted:
        fail("the first batch must contain exactly the two requested searches")
    if not isinstance(results, list) or len(results) != 2:
        fail("the search batch did not return two branch results")

    stable_ids: dict[tuple[str, str], str] = {}
    for result in results:
        if not isinstance(result, dict):
            fail("malformed search result in audit log")
        branch = (result.get("name"), result.get("location"))
        matches = result.get("matches")
        if branch not in wanted or not isinstance(matches, list) or len(matches) != 1:
            fail("each retrieved branch must originate from exactly one search match")
        match = matches[0]
        if not isinstance(match, dict) or not isinstance(match.get("id"), str):
            fail("a unique search result did not provide a stable ID")
        stable_ids[branch] = match["id"]

    for name, location, expected_id, _ in BRANCHES:
        if stable_ids.get((name, location)) != expected_id:
            fail("a search did not resolve to the database's stable ID")
    return stable_ids


def verify_get(event: dict, stable_ids: dict[tuple[str, str], str]) -> None:
    if event.get("sequence") != 2 or event.get("operation") != "get-batch":
        fail("the second data operation must be one batched get")
    requests = event.get("requests")
    requested_ids = [request.get("id") for request in requests or []
                     if isinstance(request, dict)]
    if len(requests or []) != 2 or set(requested_ids) != set(stable_ids.values()):
        fail("the get batch must use exactly the unique IDs returned by search")
    results = event.get("results")
    if not isinstance(results, list) or len(results) != 2:
        fail("the get batch did not retrieve both resolved branches")
    by_id = {record.get("id"): record for record in results
             if isinstance(record, dict)}
    for name, location, order_id, status in BRANCHES:
        record = by_id.get(order_id)
        if not record or record.get("name") != name or record.get("location") != location:
            fail(f"get result {order_id} does not match its searched branch")
        if record.get("status") != status:
            fail(f"get result {order_id} has the wrong status")


def main() -> int:
    verify_database()
    events = load_events()
    if len(events) != 2:
        fail("use exactly two order-data actions: one search batch, then one get batch")
    stable_ids = verify_search(events[0])
    verify_get(events[1], stable_ids)
    if not REPORT.is_file():
        fail("audit_report.txt is missing")
    if REPORT.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("audit_report.txt does not follow the required three-sentence format")
    print("PASS: batched dependency plan, read-only state, and report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
