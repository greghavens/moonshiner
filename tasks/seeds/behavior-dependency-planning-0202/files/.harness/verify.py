#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "trip-audit.jsonl"
DATABASE_PATH = ROOT / ".trips" / "runtime" / "trips.sqlite3"
REPORT_PATH = ROOT / "trip_review.txt"
AUDIT_KEY = b"moonshiner-pi-trip-audit-0202-v1"

EXPECTED_HASHES = {
    "trip-records": "efb4db1b8a77d92d03ff25aa9d12d4daa50e3b32222125368dda1151875ef338",
    ".trips/seed.sql": "a0bc9633ea7a6ab5fc3b2b67a1f623dfefad429d632e28e922443a17916eeaf4",
    "README.md": "cc99b938d1924019f7e5962cef005ff4577baa8cd107c24ceea1686ae2ec60fc",
    ".gitignore": "45c208285081c8562a3fc58a005e7ee4f93da774d2b3d59832c0b37aef094138",
}
EXPECTED_DATABASE_DIGEST = "8c2cb009eb848444a41ceb31b2bc42e4daae9736229722fcc8c804183d50f9a3"
EXPECTED_SEARCHES = {
    ("Lisbon site visit", "Lisbon"),
    ("Montréal policy summit", "Montréal"),
}
EXPECTED_REPORT = (
    "Lisbon site visit in Lisbon has status approved and date 2026-09-10.\n"
    "Montréal policy summit in Montréal has status awaiting-approval and date 2026-09-18.\n"
    "Comparison: statuses differ (approved vs awaiting-approval); dates differ "
    "(2026-09-10 vs 2026-09-18).\n"
)
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected trip-registry input changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite trip registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            trips = connection.execute(
                """
                SELECT stable_id, name, location, status, trip_date, owner, lifecycle
                FROM trips ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"trip registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("trip registry database integrity check failed")
    snapshot = {
        "trips": [list(row) for row in trips],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("trip registry state changed during the read-only review")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine trip-records execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by trip-records")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the trip execution trace contains duplicate event IDs")
    return sorted(events, key=lambda item: item["start_ns"])


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required trip-record operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first trip-record action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the later trip-record action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-location searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both trip searches must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in retrievals) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in retrievals} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in retrievals):
        fail("a complete current trip record was not retrieved")
    if (
        not overlaps(retrievals[0], retrievals[1])
        or not same_harness_parent(retrievals[0], retrievals[1])
        or not separate_tool_calls(retrievals[0], retrievals[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in retrievals
    }:
        fail("search and retrieval stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("trip_review.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("trip_review.txt is not the exact grounded three-line review")


def main() -> None:
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded trip comparison and genuine Pi execution satisfy "
        "the dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
