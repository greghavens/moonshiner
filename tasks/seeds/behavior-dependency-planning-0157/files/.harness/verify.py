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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "facilities-audit.jsonl"
DATABASE_PATH = ROOT / ".facilities" / "runtime" / "facilities.sqlite3"
REPORT_PATH = ROOT / "facilities-review.txt"
AUDIT_KEY = b"moonshiner-pi-facilities-audit-0157-v1"

EXPECTED_HASHES = {
    "facilities-records": "f53b3957aba1a813ab2b173e112e2a145f0ab51bf25d5304f7ca41355154b9bf",
    ".facilities/seed.sql": "ea4ef4f6ccb70597da47a9c758dbf37c597fd52317f7c8370195cdb80cef264e",
    "README.md": "209ba0ac4e7f9af456c61abc23ba05a6b05a55d4c18691a3426fd118a50c669e",
    ".gitignore": "2301851b217082006810b6bee96b8728729ad0807ebdba6cbef68e642fb6d697",
}
EXPECTED_DATABASE_DIGEST = "3485fe71f3d290f9865c699c38e6cc79e1ad27ec2cd9e3c9820663f4bea3f3de"
REQUESTED = {
    "fac-257": {
        "name": "Archive humidity check",
        "before": "assigned",
        "after": "completed",
    },
    "fac-657": {
        "name": "Rooftop garden access request",
        "before": "queued",
        "after": "assigned",
    },
}
ALLOWED_ROOT_ENTRIES = {
    ".facilities",
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    "README.md",
    "facilities-records",
    "facilities-review.txt",
}


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
            fail(f"protected facilities input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def database_snapshot() -> dict:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite facilities registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, site, status, lifecycle
                FROM facilities_records ORDER BY stable_id
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
        fail(f"facilities registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("facilities registry database integrity check failed")
    return {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }


def verify_database_state() -> None:
    digest = hashlib.sha256(canonical(database_snapshot())).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("facilities registry final state is incorrect or out-of-scope data changed")


def verify_report() -> None:
    expected = (
        "Archive humidity check (fac-257): assigned -> completed\n"
        "Rooftop garden access request (fac-657): queued -> assigned\n"
    )
    if not REPORT_PATH.is_file():
        fail("facilities-review.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("facilities-review.txt is not grounded, complete, and alphabetical")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine facilities-records execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the registry")
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
        events.append(event)
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


def verify_parallel_siblings(events: list[dict], label: str) -> None:
    if len(events) != 2:
        fail(f"{label} must contain exactly two operations")
    if (
        not overlaps(events[0], events[1])
        or not same_harness_parent(events[0], events[1])
        or not separate_tool_calls(events[0], events[1])
    ):
        fail(f"the {label} were not concurrent sibling Pi Bash calls")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 4:
        fail("expected exactly two retrievals followed by exactly two updates")
    if any(not event.get("success") for event in events):
        fail("every required facilities-records operation must succeed")

    gets, updates = events[:2], events[2:]
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the first facilities action must contain only both retrievals")
    if [event.get("operation") for event in updates] != ["update", "update"]:
        fail("the second facilities action must contain only both updates")

    if {event.get("stable_id") for event in gets} != set(REQUESTED):
        fail("the retrieval stage did not contain exactly both supplied stable IDs")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current facilities record was not retrieved")
    for event in gets:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("result_name") != expected["name"]
            or event.get("result_status") != expected["before"]
        ):
            fail("a retrieved facilities record did not establish its precondition")
    verify_parallel_siblings(gets, "complete-record retrievals")

    if min(event["start_ns"] for event in updates) <= max(
        event["end_ns"] for event in gets
    ):
        fail("an update began before both complete records were returned")
    if {event.get("stable_id") for event in updates} != set(REQUESTED):
        fail("the update stage did not contain exactly both eligible records")
    if any(event.get("result_count") != 1 for event in updates):
        fail("an eligible facilities update did not change one record")
    for event in updates:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("required_status") != expected["before"]
            or event.get("requested_status") != expected["after"]
            or event.get("before_status") != expected["before"]
            or event.get("result_status") != expected["after"]
        ):
            fail("an update did not preserve and satisfy its verified transition")
    verify_parallel_siblings(updates, "eligible updates")

    if {event["process_group_id"] for event in gets} & {
        event["process_group_id"] for event in updates
    }:
        fail("retrieval and update stages reused a Bash call")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_execution(load_events())
    verify_database_state()
    verify_report()
    print(
        "PASS: both grounded conditional facilities updates satisfy every "
        "dependency, concurrency, ordering, and negative-scope constraint"
    )


if __name__ == "__main__":
    main()
