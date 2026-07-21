#!/usr/bin/env python3
"""Protected, deterministic acceptance checks for the itinerary audit."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / ".travel-desk" / "travel.sqlite3"
ACTIVITY_PATH = ROOT / ".travel-desk" / "activity.jsonl"
REPORT_PATH = ROOT / "audit.md"
TARGETS = (
    ("Nairobi Field Training", "Nairobi"),
    ("Accra Program Review", "Accra"),
)
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    raise AssertionError(message)


def load_expected_records() -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            fail(f"travel database integrity check failed: {integrity}")
        records = []
        for name, location in TARGETS:
            rows = connection.execute(
                "SELECT id, name, location, status, date FROM trips "
                "WHERE name = ? AND location = ? ORDER BY id",
                (name, location),
            ).fetchall()
            if len(rows) != 1:
                fail(f"fixture target is not unique: {name!r} at {location!r}")
            records.append(dict(rows[0]))
        if connection.execute("SELECT COUNT(*) FROM mutation_log").fetchone()[0] != 0:
            fail("the read-only audit changed the mutation log")
        if connection.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0] != 0:
            fail("the read-only audit changed the notification log")
        return records
    finally:
        connection.close()


def load_activity() -> list[dict[str, Any]]:
    if not ACTIVITY_PATH.is_file():
        fail("no travel-desk activity was recorded")
    records = []
    for line_number, line in enumerate(
        ACTIVITY_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"activity line {line_number} is invalid JSON: {exc}")
        if not isinstance(record, dict):
            fail(f"activity line {line_number} is not an object")
        records.append(record)
    if len(records) != 4:
        fail(f"expected exactly four travel-desk data invocations, found {len(records)}")
    return records


def interval(record: dict[str, Any]) -> tuple[int, int]:
    try:
        started = int(record["started_ns"])
        finished = int(record["finished_ns"])
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"activity record has invalid timing fields: {exc}")
    if finished <= started:
        fail("activity record has a non-positive execution interval")
    return started, finished


def require_overlap(records: list[dict[str, Any]], label: str) -> None:
    if len(records) != 2:
        fail(f"{label} stage does not contain exactly two invocations")
    intervals = [interval(record) for record in records]
    if max(start for start, _ in intervals) >= min(end for _, end in intervals):
        fail(f"{label} invocations did not execute concurrently")


def verify_activity(records: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    if any(record.get("success") is not True for record in records):
        fail("every recorded travel-desk invocation must succeed")
    operations = [record.get("operation") for record in records]
    forbidden = sorted({str(op) for op in operations if op not in ALLOWED_OPERATIONS})
    if forbidden:
        fail("forbidden travel-desk operation(s) used: " + ", ".join(forbidden))

    searches = [record for record in records if record.get("operation") == "search"]
    gets = [record for record in records if record.get("operation") == "get"]
    require_overlap(searches, "search")
    require_overlap(gets, "get")

    expected_by_pair = {(row["name"], row["location"]): row for row in expected}
    returned_ids: dict[tuple[str, str], str] = {}
    for record in searches:
        inputs = record.get("inputs")
        if not isinstance(inputs, dict):
            fail("search activity is missing its inputs")
        pair = (inputs.get("name"), inputs.get("location"))
        if pair not in expected_by_pair or pair in returned_ids:
            fail(f"unexpected or duplicate search branch: {pair!r}")
        output = record.get("output")
        matches = output.get("matches") if isinstance(output, dict) else None
        if not isinstance(matches, list) or len(matches) != 1:
            fail(f"search branch did not return exactly one match: {pair!r}")
        match = matches[0]
        expected_row = expected_by_pair[pair]
        if not isinstance(match, dict) or match != {
            "id": expected_row["id"],
            "name": expected_row["name"],
            "location": expected_row["location"],
        }:
            fail(f"search result does not match the database record: {pair!r}")
        returned_ids[pair] = match["id"]

    expected_by_id = {row["id"]: row for row in expected}
    seen_gets: set[str] = set()
    for record in gets:
        inputs = record.get("inputs")
        stable_id = inputs.get("id") if isinstance(inputs, dict) else None
        if stable_id not in expected_by_id or stable_id in seen_gets:
            fail(f"unexpected or duplicate get branch: {stable_id!r}")
        if stable_id not in returned_ids.values():
            fail(f"get ID was not produced by a unique search: {stable_id!r}")
        if record.get("output") != expected_by_id[stable_id]:
            fail(f"get output does not match the full database record: {stable_id!r}")
        seen_gets.add(stable_id)

    search_finished = max(interval(record)[1] for record in searches)
    get_started = min(interval(record)[0] for record in gets)
    if get_started <= search_finished:
        fail("a get started before both search results had returned")


def verify_report(expected: list[dict[str, Any]]) -> None:
    if not REPORT_PATH.is_file():
        fail("audit.md is missing")
    first, second = expected
    relation = "same" if first["status"] == second["status"] else "different"
    required = (
        f"- {first['name']} | ID: {first['id']} | status: {first['status']}\n"
        f"- {second['name']} | ID: {second['id']} | status: {second['status']}\n"
        f"- Status comparison | {relation} | {first['id']}: {first['status']} | "
        f"{second['id']}: {second['status']}\n"
    )
    actual = REPORT_PATH.read_text(encoding="utf-8")
    if actual != required:
        fail("audit.md does not have the required three-bullet content and exact format")


def main() -> int:
    try:
        expected = load_expected_records()
        activity = load_activity()
        verify_activity(activity, expected)
        verify_report(expected)
    except (AssertionError, OSError, sqlite3.Error) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: read-only dependency plan and exact audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
