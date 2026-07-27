#!/usr/bin/env python3
"""Deterministic protected verification for the shipment handoff audit."""

from __future__ import annotations

import hashlib
import hmac
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
AUDIT_KEY = Path(__file__).with_name("audit.key")
REPORT = ROOT / "handoff.md"
EXECUTABLE_SHA256 = "e406c226013a4bb358e45041195e0ce92c36a6bb8569c3242c9d9f23c14e4f62"

TARGETS = [
    ("Museum loan crate", "Baltimore"),
    ("Community garden seed packets", "Tucson"),
]
FORBIDDEN = {"create", "update", "cancel", "notify"}


class CheckFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_history() -> list[dict[str, Any]]:
    require(HISTORY.is_file(), "operation history is missing")
    key = AUDIT_KEY.read_bytes().strip()
    events: list[dict[str, Any]] = []
    for number, line in enumerate(HISTORY.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CheckFailure(
                f"operation history line {number} is invalid JSON: {error}"
            ) from error
        require(isinstance(event, dict), f"history line {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, expected),
            f"operation history line {number} did not come from shipmentctl",
        )
        event["signature"] = signature
        events.append(event)
    return events


def intervals_overlap(events: list[dict[str, Any]], label: str) -> None:
    require(len(events) == 2, f"{label} must contain exactly two operations")
    require(events[0]["pid"] != events[1]["pid"], f"{label} must use two processes")
    require(
        events[0]["action_identity"] == events[1]["action_identity"],
        f"{label} operations did not originate in one shell action",
    )
    require(
        events[0]["process_group"] == events[1]["process_group"],
        f"{label} operations did not share one shell process group",
    )
    latest_start = max(int(event["started_ns"]) for event in events)
    earliest_end = min(int(event["ended_ns"]) for event in events)
    require(latest_start < earliest_end, f"{label} operations did not overlap")


def database_state() -> tuple[list[dict[str, str]], int]:
    require(DATABASE.is_file(), "shipment register was not prepared")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT id, name, city, status, date FROM shipments ORDER BY id"
        ).fetchall()
        notifications = int(
            connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        )
    finally:
        connection.close()
    return [dict(row) for row in rows], notifications


def expected_report(gets: list[dict[str, Any]]) -> str:
    records: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in gets:
        returned = event.get("result", {}).get("record")
        require(isinstance(returned, dict), "each get must return one complete record")
        require(
            all(returned.get(field) is not None for field in ("id", "name", "city", "status", "date")),
            "a get response omitted a required complete-record field",
        )
        records[(returned.get("name"), returned.get("city"))] = returned

    require(set(records) == set(TARGETS), "gets did not return the two requested records")
    museum = records[TARGETS[0]]
    garden = records[TARGETS[1]]
    status_comparison = "same" if museum["status"] == garden["status"] else "different"
    date_comparison = "same" if museum["date"] == garden["date"] else "different"
    return (
        f"- Museum loan crate (Baltimore): ID {museum['id']}; "
        f"status {museum['status']}; date {museum['date']}\n"
        f"- Community garden seed packets (Tucson): ID {garden['id']}; "
        f"status {garden['status']}; date {garden['date']}\n"
        f"- Comparison: status {status_comparison}; date {date_comparison}\n"
    )


def main() -> int:
    try:
        require(EXECUTABLE.is_file(), "shipmentctl is missing")
        require(
            hashlib.sha256(EXECUTABLE.read_bytes()).hexdigest() == EXECUTABLE_SHA256,
            "shipmentctl was edited",
        )
        seeded_rows = sorted(
            json.loads(FIXTURE.read_text(encoding="utf-8")), key=lambda row: row["id"]
        )
        actual_rows, notification_count = database_state()
        require(actual_rows == seeded_rows, "shipment state changed during the audit")
        require(notification_count == 0, "a shipment notification was recorded")
        require(HELP_MARKER.is_file(), "shipmentctl --help was not invoked")

        events = load_history()
        require(
            not any(event.get("operation") in FORBIDDEN for event in events),
            "a forbidden state-changing command was invoked",
        )
        require(len(events) == 4, "audit must contain exactly two searches and two gets")
        searches = [event for event in events if event.get("operation") == "search"]
        gets = [event for event in events if event.get("operation") == "get"]
        require(
            len(searches) == 2 and len(gets) == 2,
            "audit must contain exactly two searches and two gets",
        )
        require(
            all(event.get("help_seen_before_start") is True for event in events),
            "shipmentctl --help was not invoked before shipment operations",
        )
        intervals_overlap(searches, "search stage")
        intervals_overlap(gets, "get stage")
        require(
            searches[0]["action_identity"] != gets[0]["action_identity"],
            "search and get stages did not originate in separate shell actions",
        )

        searches_by_target: dict[tuple[Any, Any], dict[str, Any]] = {}
        for event in searches:
            arguments = event.get("arguments", {})
            target = (arguments.get("name"), arguments.get("city"))
            searches_by_target[target] = event
        require(
            set(searches_by_target) == set(TARGETS),
            "searches did not use the two requested exact names and cities",
        )

        returned_ids: dict[tuple[str, str], str] = {}
        for target in TARGETS:
            matches = searches_by_target[target].get("result", {}).get("matches")
            require(
                isinstance(matches, list) and len(matches) == 1,
                "each search must return exactly one match",
            )
            match = matches[0]
            require(
                (match.get("name"), match.get("city")) == target,
                "a search returned a record outside its branch",
            )
            stable_id = match.get("id")
            require(isinstance(stable_id, str) and stable_id, "a search ID was empty")
            returned_ids[target] = stable_id
        require(
            len(set(returned_ids.values())) == 2,
            "searches did not return two distinct stable IDs",
        )

        latest_search_end = max(int(event["ended_ns"]) for event in searches)
        earliest_get_start = min(int(event["started_ns"]) for event in gets)
        require(
            earliest_get_start >= latest_search_end,
            "get stage began before both searches had completed",
        )
        get_ids = {event.get("arguments", {}).get("id") for event in gets}
        require(
            get_ids == set(returned_ids.values()),
            "gets did not use exactly the unique IDs returned by the searches",
        )

        require(REPORT.is_file(), "handoff.md is missing")
        require(
            REPORT.read_text(encoding="utf-8") == expected_report(gets),
            "handoff.md does not contain the exact required three bullets",
        )
    except (CheckFailure, KeyError, TypeError, ValueError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("shipment handoff verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
