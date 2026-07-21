#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for the subscription audit."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "subscriptions.db"
AUDIT = ROOT / ".subscription-audit.jsonl"
REPORT = ROOT / "audit_report.txt"
TARGETS = (
    ("Depot Radio Data Subscription", "Fleet Operations"),
    ("Library Hotspot Subscription", "Outreach Services"),
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def expected_records() -> dict[tuple[str, str], dict[str, str]]:
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        resolved: dict[tuple[str, str], dict[str, str]] = {}
        for name, location in TARGETS:
            rows = db.execute(
                "SELECT stable_id, status FROM subscriptions WHERE name = ? AND location = ?",
                (name, location),
            ).fetchall()
            if len(rows) != 1:
                fail("protected fixture no longer has one record for each requested branch")
            resolved[(name, location)] = dict(rows[0])
    return resolved


def load_events() -> list[dict[str, Any]]:
    if not AUDIT.exists():
        fail("no executable audit trail was produced")
    events: list[dict[str, Any]] = []
    for number, raw in enumerate(AUDIT.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit trail line {number} is not valid JSON")
        if value == {"format": "subscription-audit-v1"}:
            if number != 1:
                fail("audit trail header is misplaced")
            continue
        if not isinstance(value, dict):
            fail(f"audit trail line {number} is not an event object")
        events.append(value)
    return events


def paired_operations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(events) != 8:
        fail("the audit must contain exactly two searches and two gets")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        operation_id = event.get("op_id")
        if not isinstance(operation_id, str) or not operation_id:
            fail("an audit event has no operation ID")
        grouped[operation_id].append(event)
    if len(grouped) != 4:
        fail("expected exactly four executable invocations")

    operations: list[dict[str, Any]] = []
    for operation_id, pair in grouped.items():
        starts = [event for event in pair if event.get("phase") == "start"]
        ends = [event for event in pair if event.get("phase") == "end"]
        if len(starts) != 1 or len(ends) != 1 or len(pair) != 2:
            fail(f"operation {operation_id} lacks one start and one end event")
        start, end = starts[0], ends[0]
        if start.get("op") != end.get("op") or start.get("pid") != end.get("pid"):
            fail(f"operation {operation_id} has inconsistent event identity")
        if not isinstance(start.get("at_ns"), int) or not isinstance(end.get("at_ns"), int):
            fail(f"operation {operation_id} has an invalid clock value")
        if end["at_ns"] - start["at_ns"] < 200_000_000:
            fail(f"operation {operation_id} did not execute the client workflow")
        operations.append({"start": start, "end": end})
    return operations


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["start"]["at_ns"], second["start"]["at_ns"]) < min(
        first["end"]["at_ns"], second["end"]["at_ns"]
    )


def verify_execution(operations: list[dict[str, Any]], expected: dict[tuple[str, str], dict[str, str]]) -> None:
    forbidden = sorted({
        operation["start"].get("op")
        for operation in operations
        if operation["start"].get("op") not in {"search", "get"}
    })
    if forbidden:
        fail("forbidden operation executed: " + ", ".join(str(item) for item in forbidden))

    searches = [operation for operation in operations if operation["start"]["op"] == "search"]
    gets = [operation for operation in operations if operation["start"]["op"] == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("expected exactly two searches followed by two gets")
    if searches[0]["start"]["pid"] == searches[1]["start"]["pid"] or not overlaps(*searches):
        fail("the two independent searches were not executed concurrently")
    if gets[0]["start"]["pid"] == gets[1]["start"]["pid"] or not overlaps(*gets):
        fail("the two eligible gets were not executed concurrently")
    if min(item["start"]["at_ns"] for item in gets) <= max(
        item["end"]["at_ns"] for item in searches
    ):
        fail("a get began before both searches had returned")

    observed_searches: set[tuple[str, str]] = set()
    for operation in searches:
        start, end = operation["start"], operation["end"]
        key = (start.get("name"), start.get("location"))
        if key not in expected or key in observed_searches:
            fail("searches did not cover each requested name/location pair exactly once")
        observed_searches.add(key)
        record = expected[key]
        if end.get("count") != 1 or end.get("ids") != [record["stable_id"]]:
            fail("a search result was not carried into its dependent branch")

    expected_ids = {record["stable_id"] for record in expected.values()}
    observed_ids: set[str] = set()
    for operation in gets:
        start, end = operation["start"], operation["end"]
        stable_id = start.get("stable_id")
        if stable_id not in expected_ids or stable_id in observed_ids:
            fail("gets did not use the two uniquely resolved stable IDs")
        observed_ids.add(stable_id)
        record = next(value for value in expected.values() if value["stable_id"] == stable_id)
        if end.get("found") is not True or end.get("stable_id") != stable_id:
            fail("a uniquely resolved record was not retrieved")
        if end.get("status") != record["status"]:
            fail("the retrieved status does not match the protected record")


def verify_report(expected: dict[tuple[str, str], dict[str, str]]) -> None:
    if not REPORT.is_file():
        fail("audit_report.txt is missing")
    report = REPORT.read_text(encoding="utf-8").strip()
    words = re.findall(r"\b[\w'-]+\b", report, flags=re.UNICODE)
    if len(words) >= 75:
        fail(f"report has {len(words)} words; it must be under 75")
    if not report.endswith("No changes made."):
        fail('report must end with the exact sentence "No changes made."')
    for record in expected.values():
        if record["stable_id"] not in report:
            fail("report omits a resolved stable ID")
        if record["status"].casefold() not in report.casefold():
            fail("report omits a retrieved status value")
    statuses = {record["status"].casefold() for record in expected.values()}
    lowered = report.casefold()
    if len(statuses) == 1:
        if not re.search(r"\b(same|match(?:es|ing)?|both)\b", lowered):
            fail("report does not compare the equal status values")
    elif not re.search(r"\b(differ|differs|different|unequal)\b|not the same", lowered):
        fail("report does not compare the differing status values")


def main() -> int:
    expected = expected_records()
    operations = paired_operations(load_events())
    verify_execution(operations, expected)
    verify_report(expected)
    print("PASS: genuine parallel search/get audit and constrained report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
