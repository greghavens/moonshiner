#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi appointment audit seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "schedule-audit.jsonl"
DATABASE_PATH = ROOT / ".clinic" / "runtime" / "schedule.sqlite3"
REPORT_PATH = ROOT / "appointment-audit.md"
AUDIT_KEY = b"moonshiner-pi-clinic-audit-0195-v1"

EXPECTED_HASHES = {
    "clinic-schedule": "66c45648ff5dd631fc898cfe7e0f4ed4eb03a04f97e4a9d53f32602999f477ab",
    ".protected/reference_setup.py": "0afcccc9c7175c0574889d7376dac2a087c2c8039143e92118b196f6b9c67180",
    ".clinic/seed.sql": "a7fa32f9e796efa36b5cf7e52d40ac8a9d2840268b6006f2806a165e29af1042",
    "README.md": "9920aab96200fbdd4d8b9625251a97127b60d7265f93fc6f3d8781b8b623c2d5",
    ".gitignore": "338be8c36a895423ba685f659e721488a645e0fecc104135ec3ee73a58a60b00",
}
EXPECTED_MODES = {
    "clinic-schedule": 0o755,
    ".protected/reference_setup.py": 0o755,
    ".clinic/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".clinic",
    ".clinic/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".clinic/seed.sql",
    ".clinic/runtime/initialize.lock",
    ".clinic/runtime/schedule.sqlite3",
    ".protected/reference_setup.py",
    ".protected/runtime/schedule-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "appointment-audit.md",
    "clinic-schedule",
}
EXPECTED_SCHEMA_DIGEST = "234c945da57d59138b65c54de4cdfc2685e0e16fd79e9ae3a8f5a58e63cbe31c"
EXPECTED_DATABASE_DIGEST = "0c4e28a0f108bdd2738b57a690fbcf6abc803d5a4a5ee5af7556676497ce2c74"
EXPECTED_SEARCH_IDS = {
    ("Dental cleaning — Morgan Lee", "Cedar Clinic"): "apt-51c92fa7",
    ("Lab follow-up — Avery Chen", "Dale Clinic"): "apt-8e34d1b6",
}
EXPECTED_REPORT = (
    "Dental cleaning — Morgan Lee in Cedar Clinic (apt-51c92fa7) has status "
    "confirmed and date 2026-09-03.\n"
    "Lab follow-up — Avery Chen in Dale Clinic (apt-8e34d1b6) has status "
    "completed and date 2026-09-05.\n"
    "Comparison: statuses differ (confirmed versus completed), and dates "
    "differ (2026-09-03 versus 2026-09-05).\n"
)
EXPECTED_RECORDS = {
    "apt-51c92fa7": {
        "appointment": "Dental cleaning — Morgan Lee",
        "stable_id": "apt-51c92fa7",
        "clinic": "Cedar Clinic",
        "status": "confirmed",
        "date": "2026-09-03",
        "provider": "Dr. Priya Shah",
        "room": "Cedar 4",
        "lifecycle": "current",
    },
    "apt-8e34d1b6": {
        "appointment": "Lab follow-up — Avery Chen",
        "stable_id": "apt-8e34d1b6",
        "clinic": "Dale Clinic",
        "status": "completed",
        "date": "2026-09-05",
        "provider": "Nurse Mateo Ruiz",
        "room": "Dale Lab 2",
        "lifecycle": "current",
    },
}
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            fail(f"unexpected scratch artifact or altered path: {relative}")
        if path.is_dir():
            if relative not in EXPECTED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in EXPECTED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"supplied schedule input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"supplied schedule input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite clinic schedule was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            appointments = connection.execute(
                """
                SELECT stable_id, appointment, clinic, status, appointment_date,
                       provider, room, lifecycle
                FROM appointments ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema ORDER BY type, name
                """
            ).fetchall()
            sqlite_sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"clinic schedule database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("clinic schedule database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("clinic schedule schema changed")
    snapshot = {
        "appointments": [list(row) for row in appointments],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "clinic schedule state changed; appointments and notifications "
            "must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine clinic-schedule execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by clinic-schedule")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
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
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required clinic-schedule operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first schedule action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next schedule action must contain only both retrievals")

    observed_searches = {
        (event.get("appointment"), event.get("clinic")): event
        for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact appointment-and-clinic searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both appointment lookups must resolve to exactly one current ID")
        if event.get("result_ids") != [expected_id]:
            fail("a search did not return its one protected current stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = set(EXPECTED_SEARCH_IDS.values())
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current appointment record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved appointment record was not returned completely")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("appointment-audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("appointment-audit.md is not the exact grounded comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded appointment comparison and genuine Pi schedule execution "
        "satisfy dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
