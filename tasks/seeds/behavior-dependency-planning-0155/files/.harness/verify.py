#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "__pycache__" / "clinic-audit.jsonl"
DATABASE_PATH = ROOT / ".clinic" / "__pycache__" / "clinic.sqlite3"
MARKER_PATH = ROOT / ".reference_solution"
AUDIT_KEY = b"moonshiner-pi-healthcare-audit-0155-v1"
MARKER_DIGEST = "285bbf980e9e189d3e76a1a22391137b632a00f73430919b85b7f4e518890cb4"
EXPECTED_HASHES = {
    "clinic-records": "23cf4fa7a15c95bbb01a9e938511150ebca235574998e5c38eb78bb6e4fa915a",
    ".clinic/seed.sql": "df2bd3c5e7f64a527331fdf59c7c6fcbb15c08635da3dedd6270945bc32962e6",
    ".harness/reference_setup.py": "37b46b5980bd4299e89284fa97dc26b4ba69a6797cd403eec8fe5c98428fb6e7",
    "README.md": "42a6c500b6746f06143a8beb731c7436fa05bfde0e3b7a4ba6979d411be1dbeb",
    ".gitignore": "0271c479d42e246d535d4407c106b317bb0c561ad615be99abfd44fff44f1ac3",
}
EXPECTED_DATABASE_DIGEST = "f5ff9d48edd40647cafae78ed138f939c1050c701d77ffc1ea41af3fa14c5fa7"
REQUESTED = {
    "hea-255": {
        "title": "Physical therapy",
        "patient": "Alex Green",
        "before": "confirmed",
        "after": "completed",
    },
    "hea-655": {
        "title": "Imaging appointment",
        "patient": "Casey Bell",
        "before": "requested",
        "after": "confirmed",
    },
}
EXPECTED_PATHS = {
    ".clinic",
    ".clinic/__pycache__",
    ".clinic/__pycache__/clinic.sqlite3",
    ".clinic/__pycache__/initialize.lock",
    ".clinic/seed.sql",
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/__pycache__",
    ".harness/__pycache__/clinic-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "clinic-records",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected clinic input changed: {relative}")


def verify_scope() -> None:
    expected = set(EXPECTED_PATHS)
    if MARKER_PATH.exists():
        if not MARKER_PATH.is_file() or file_sha256(MARKER_PATH) != MARKER_DIGEST:
            fail("unexpected or altered reference marker")
        expected.add(".reference_solution")

    observed: set[str] = set()
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts:
            continue
        observed.add(relative.as_posix())
    unexpected = sorted(observed - expected)
    missing = sorted(expected - observed)
    if unexpected:
        fail("unexpected scratch artifact: " + ", ".join(unexpected))
    if missing:
        fail("required sandbox artifact is missing: " + ", ".join(missing))


def database_snapshot() -> dict:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite clinic service was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            user_version = connection.execute("PRAGMA user_version").fetchone()[0]
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            ).fetchall()
            records = connection.execute(
                """
                SELECT stable_id, title, patient, status, scheduled_for, lifecycle
                FROM clinic_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, channel, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"clinic database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("clinic database integrity check failed")
    return {
        "user_version": user_version,
        "schema": [list(row) for row in schema],
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }


def verify_database_state() -> None:
    digest = hashlib.sha256(canonical(database_snapshot())).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("clinic final state is incorrect or out-of-scope data changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine clinic-records execution trace was produced")
    events: list[dict] = []
    seen_ids: set[str] = set()
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
            fail(f"audit event {number} was not emitted intact by the client")

        event_id = event.get("event_id")
        try:
            uuid.UUID(event_id)
        except (AttributeError, TypeError, ValueError):
            fail(f"audit event {number} has an invalid event ID")
        if event_id in seen_ids:
            fail("the execution trace contains a duplicate event ID")
        seen_ids.add(event_id)

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
        for field in ("process_start_ticks", "parent_start_ticks"):
            if not isinstance(event.get(field), str) or event[field] == "unavailable":
                fail(f"audit event {number} lacks stable process evidence")
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
    if len(events) != 5:
        fail("expected one root-help call and exactly two retrievals then two updates")
    if any(event.get("success") is not True for event in events):
        fail("every required clinic-records operation must succeed")

    help_event = events[0]
    gets = events[1:3]
    updates = events[3:5]
    if help_event.get("operation") != "help":
        fail("the client root help was not completed before clinic-data access")
    if help_event["end_ns"] >= min(event["start_ns"] for event in gets):
        fail("clinic-data access began before the root-help call finished")

    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the first clinic-data action must contain only both retrievals")
    if {event.get("stable_id") for event in gets} != set(REQUESTED):
        fail("the retrieval stage did not contain exactly both supplied stable IDs")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current clinic record was not retrieved")
    for event in gets:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("result_title") != expected["title"]
            or event.get("result_patient") != expected["patient"]
            or event.get("result_status") != expected["before"]
        ):
            fail("a retrieved clinic record did not establish its own precondition")
    verify_parallel_siblings(gets, "complete-record retrievals")

    if min(event["start_ns"] for event in updates) <= max(
        event["end_ns"] for event in gets
    ):
        fail("an update began before both complete records were returned")
    if [event.get("operation") for event in updates] != ["update", "update"]:
        fail("the second clinic-data action must contain only both updates")
    if {event.get("stable_id") for event in updates} != set(REQUESTED):
        fail("the update stage did not contain exactly both eligible records")
    for event in updates:
        expected = REQUESTED[event["stable_id"]]
        if (
            event.get("required_status") != expected["before"]
            or event.get("requested_status") != expected["after"]
            or event.get("before_status") != expected["before"]
            or event.get("result_status") != expected["after"]
            or event.get("result_count") != 1
            or event.get("changed") is not True
        ):
            fail("an update did not preserve and satisfy its verified transition")
    verify_parallel_siblings(updates, "eligible guarded updates")

    get_groups = {event["process_group_id"] for event in gets}
    update_groups = {event["process_group_id"] for event in updates}
    if get_groups & update_groups:
        fail("retrieval and update stages reused a Bash call")
    if help_event["process_group_id"] in get_groups | update_groups:
        fail("the root-help and clinic-data stages reused a Bash call")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_execution(load_events())
    verify_database_state()
    print(
        "PASS: both grounded conditional clinic updates satisfy every "
        "dependency, concurrency, ordering, and negative-scope constraint"
    )


if __name__ == "__main__":
    main()
