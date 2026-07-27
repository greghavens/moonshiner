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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expense-audit.jsonl"
DATABASE_PATH = ROOT / ".expenses" / "runtime" / "expenses.sqlite3"
REPORT_PATH = ROOT / "expense-reconciliation.txt"
AUDIT_KEY = b"moonshiner-pi-expense-audit-0154-v1"

EXPECTED_HASHES = {
    "expense-registry": "38efadfd53ef71abdfdcdf71ed9af5a20f9f4f7a60c9192803e63130d8137faf",
    ".expenses/seed.sql": "d4e3bebe67cfd7c7a594b1d4617b63d5d56140ca400e3f0c6091508baad8fc5b",
    "README.md": "c5e192085e360518ad87e53f87cbab0a3b35d29e565d81fe62d3f4b15e65e3c1",
    ".gitignore": "31b793125a3ea9f34c7cb3e6d340ec060e118975fd8a8db5eae1a99fdbb42aae",
}
EXPECTED_DATABASE_DIGEST = "4ded1c41f5523e30ab05875fbcab74e475a6cbd996d86518d30d3b7369f7052e"
REQUESTED = {
    "exp-254": {
        "name": "Community printing invoice",
        "before": "approved",
        "after": "audit-cleared",
    },
    "exp-654": {
        "name": "Mentor breakfast receipt",
        "before": "submitted",
        "after": "approved",
    },
}
ALLOWED_ROOT_ENTRIES = {
    ".expenses",
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    "README.md",
    "expense-registry",
    "expense-reconciliation.txt",
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
            fail(f"protected expense-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def database_snapshot() -> dict:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, status, lifecycle
                FROM expense_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            profile = connection.execute(
                "SELECT profile_key, profile_value FROM profile ORDER BY profile_key"
            ).fetchall()
            availability = connection.execute(
                "SELECT location, available FROM availability ORDER BY location"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expense registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("expense registry database integrity check failed")
    return {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
        "profile": [list(row) for row in profile],
        "availability": [list(row) for row in availability],
    }


def verify_database_state() -> None:
    digest = hashlib.sha256(canonical(database_snapshot())).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("expense registry final state is incorrect or out-of-scope data changed")


def verify_report() -> None:
    expected = (
        "Community printing invoice (exp-254): approved -> audit-cleared\n"
        "Mentor breakfast receipt (exp-654): submitted -> approved\n"
    )
    if not REPORT_PATH.is_file():
        fail("expense-reconciliation.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("expense-reconciliation.txt is not grounded in the returned transitions")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expense-registry execution trace was produced")
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
        fail("every required expense-registry operation must succeed")

    gets, updates = events[:2], events[2:]
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the first registry action must contain only both retrievals")
    if [event.get("operation") for event in updates] != ["update", "update"]:
        fail("the second registry action must contain only both updates")

    if {event.get("stable_id") for event in gets} != set(REQUESTED):
        fail("the retrieval stage did not contain exactly both supplied stable IDs")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current expense record was not retrieved")
    for event in gets:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("result_name") != expected["name"]
            or event.get("result_status") != expected["before"]
        ):
            fail("a retrieved record did not establish its update precondition")
    verify_parallel_siblings(gets, "complete-record retrievals")

    if min(event["start_ns"] for event in updates) <= max(
        event["end_ns"] for event in gets
    ):
        fail("an update began before both complete records were returned")
    if {event.get("stable_id") for event in updates} != set(REQUESTED):
        fail("the update stage did not contain exactly both eligible records")
    if any(event.get("result_count") != 1 for event in updates):
        fail("an eligible expense update did not return one updated record")
    for event in updates:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("requested_status") != expected["after"]
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
        "PASS: both grounded conditional expense updates satisfy every "
        "dependency, concurrency, ordering, and negative-scope constraint"
    )


if __name__ == "__main__":
    main()
