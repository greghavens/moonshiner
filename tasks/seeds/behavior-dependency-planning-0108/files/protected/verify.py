#!/usr/bin/env python3
"""Protected, deterministic verification for the shipment audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
DATABASE = STATE / "shipments.sqlite3"
HISTORY = STATE / "operations.jsonl"
HELP_MARKER = STATE / "help-used"
EXECUTABLE = ROOT / "shipmentctl"
FIXTURE = Path(__file__).with_name("records.json")
REPORT = ROOT / "audit.md"
EXECUTABLE_SHA256 = "fd4bc14cc0ff3b4588a9ead14ca572afc31f4185160f2d7894cfc9bbeca2e31d"

TARGETS = {
    ("Riverbend sample shipment", "Portland"),
    ("Aurora book donation", "Denver"),
}
FORBIDDEN = {"create", "update", "cancel", "notify"}


class CheckFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def load_history() -> list[dict[str, Any]]:
    require(HISTORY.is_file(), "operation history is missing")
    events = []
    for number, line in enumerate(HISTORY.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CheckFailure(f"operation history line {number} is invalid JSON: {error}")
        require(isinstance(event, dict), f"operation history line {number} is not an object")
        events.append(event)
    return events


def intervals_overlap(events: list[dict[str, Any]], label: str) -> None:
    require(len(events) == 2, f"{label} must contain exactly two operations")
    require(events[0]["pid"] != events[1]["pid"], f"{label} must use two processes")
    require(
        events[0]["parent_pid"] == events[1]["parent_pid"],
        f"{label} operations did not originate in one shell action",
    )
    require(
        events[0]["process_group"] == events[1]["process_group"],
        f"{label} operations did not share one shell process group",
    )
    latest_start = max(int(event["started_ns"]) for event in events)
    earliest_end = min(int(event["ended_ns"]) for event in events)
    require(latest_start < earliest_end, f"{label} operations did not overlap")


def database_rows() -> tuple[list[dict[str, str]], int]:
    require(DATABASE.is_file(), "state database was not prepared")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, name, city, status FROM shipments ORDER BY id"
        ).fetchall()
        notifications = connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    finally:
        connection.close()
    return ([dict(row) for row in rows], int(notifications))


def expected_report(gets: list[dict[str, Any]]) -> str:
    records = {}
    for event in gets:
        record = event.get("result", {}).get("record")
        require(isinstance(record, dict), "each get must return one record")
        records[(record.get("name"), record.get("city"))] = record

    require(set(records) == TARGETS, "get results are not the two requested records")
    river = records[("Riverbend sample shipment", "Portland")]
    aurora = records[("Aurora book donation", "Denver")]
    comparison = "match" if river["status"] == aurora["status"] else "differ"
    return (
        f"- Riverbend sample shipment: ID {river['id']}; status {river['status']}\n"
        f"- Aurora book donation: ID {aurora['id']}; status {aurora['status']}\n"
        f"- Result: statuses {comparison}\n"
    )


def main() -> int:
    try:
        require(EXECUTABLE.is_file(), "shipmentctl is missing")
        require(
            hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest() == EXECUTABLE_SHA256,
            "shipmentctl was edited",
        )
        actual_rows, notification_count = database_rows()
        seeded_rows = sorted(
            json.loads(FIXTURE.read_text(encoding="utf-8")), key=lambda row: row["id"]
        )
        require(actual_rows == seeded_rows, "shipment state changed during the audit")
        require(notification_count == 0, "a notification was recorded")
        require(HELP_MARKER.is_file(), "shipmentctl --help was not invoked")

        events = load_history()
        require(not any(event.get("operation") in FORBIDDEN for event in events),
                "a forbidden state-changing command was invoked")
        require(len(events) == 4, "the audit must contain exactly two searches and two gets")
        searches = [event for event in events if event.get("operation") == "search"]
        gets = [event for event in events if event.get("operation") == "get"]
        require(len(searches) == 2 and len(gets) == 2,
                "the audit must contain exactly two searches and two gets")
        intervals_overlap(searches, "search stage")
        intervals_overlap(gets, "get stage")
        require(
            searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "search and get stages did not originate in separate shell actions",
        )

        requested = {
            (event.get("arguments", {}).get("name"), event.get("arguments", {}).get("city"))
            for event in searches
        }
        require(requested == TARGETS, "searches did not use the requested names and cities")

        returned_ids = set()
        for event in searches:
            matches = event.get("result", {}).get("matches")
            require(isinstance(matches, list) and len(matches) == 1,
                    "each search must return exactly one match")
            returned_ids.add(matches[0].get("id"))
        require(len(returned_ids) == 2 and None not in returned_ids,
                "searches did not return two unique IDs")

        latest_search_end = max(int(event["ended_ns"]) for event in searches)
        earliest_get_start = min(int(event["started_ns"]) for event in gets)
        require(earliest_get_start >= latest_search_end,
                "get stage began before both searches completed")
        get_ids = {event.get("arguments", {}).get("id") for event in gets}
        require(get_ids == returned_ids, "get stage did not use exactly the returned IDs")

        require(REPORT.is_file(), "audit.md is missing")
        require(REPORT.read_text(encoding="utf-8") == expected_report(gets),
                "audit.md does not have the exact required three bullets")
    except (CheckFailure, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("shipment audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
