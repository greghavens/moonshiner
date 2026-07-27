#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi course dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "course-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".courses" / "runtime" / "courses.sqlite3"
SEED_PATH = ROOT / ".courses" / "seed.sql"
REPORT_PATH = ROOT / "course-handoff.md"

EXPECTED_HASHES = {
    "course-registry": "a6706c518143ad78d5bc20d6af6459675b6ae7f6fcaa7893a48653183a32ae8a",
    ".protected/audit.key": "73ccbb7daa9ff1fbc8d4094f054f0d6ea1743cdf0d7c980fa0012d13c9b212cc",
    ".protected/reference_setup.py": "eceeeb3df7f2418d5a898d7987b3c1b509b7fdeb0ff86fd274d17dc581c94a05",
    ".courses/seed.sql": "01d4d3faecd90cfee0f38d4c9ad8c7ddeaf70b29f7d6a525be888814f96d5f7d",
    "README.md": "32697b4dfb73a6847dc212a7bf77c22deafcc89ac263186e65f8547650c5a41a",
    ".gitignore": "538e432e73b33823c2569cd567cc91c7b4cc8141921af480783fb1112710125b",
}
EXPECTED_MODES = {
    "course-registry": 0o755,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    ".courses/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".courses",
    ".courses/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".courses/runtime/courses.sqlite3",
    ".courses/runtime/initialize.lock",
    ".courses/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/course-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "course-handoff.md",
    "course-registry",
}
TARGET_SCOPES = (
    ("Wetland field methods", "River Campus"),
    ("Introductory American Sign Language", "Central Campus"),
)
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
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
            fail(f"supplied course-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"supplied course-registry input mode changed: {relative}")


def database_snapshot(connection: sqlite3.Connection) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        fail("course registry database integrity check failed")
    schema = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema ORDER BY type, name
        """
    ).fetchall()
    courses = connection.execute(
        """
        SELECT stable_id, title, campus, status, course_date,
               instructor, schedule, credits, notes, lifecycle
        FROM courses ORDER BY stable_id
        """
    ).fetchall()
    notifications = connection.execute(
        """
        SELECT notification_id, stable_id, message
        FROM notifications ORDER BY notification_id
        """
    ).fetchall()
    sequences = connection.execute(
        "SELECT name, seq FROM sqlite_sequence ORDER BY name"
    ).fetchall()
    return {
        "schema": [list(row) for row in schema],
        "courses": [list(row) for row in courses],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequences],
    }


def expected_state() -> tuple[dict, dict[tuple[str, str], dict]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        connection.commit()
        snapshot = database_snapshot(connection)
        records: dict[tuple[str, str], dict] = {}
        for title, campus in TARGET_SCOPES:
            rows = connection.execute(
                """
                SELECT title, stable_id, campus, status, course_date,
                       instructor, schedule, credits, notes, lifecycle
                FROM courses
                WHERE title = ? AND campus = ? AND lifecycle = 'current'
                ORDER BY stable_id
                """,
                (title, campus),
            ).fetchall()
            if len(rows) != 1:
                fail("protected seed no longer has one current target per scope")
            row = rows[0]
            records[(title, campus)] = {
                "title": row[0],
                "stable_id": row[1],
                "campus": row[2],
                "status": row[3],
                "date": row[4],
                "instructor": row[5],
                "schedule": row[6],
                "credits": row[7],
                "notes": row[8],
                "lifecycle": row[9],
            }
        return snapshot, records
    finally:
        connection.close()


def verify_database_state(expected: dict) -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite course registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            actual = database_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"course registry database is unreadable: {exc}")
    if actual != expected:
        fail("course or notification state changed during the read-only handoff")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine course-registry execution trace was produced")
    events = []
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        event = dict(signed_event)
        signature = event.pop("signature", None)
        expected_signature = hmac.new(
            audit_key, canonical(event), hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected_signature
        ):
            fail(f"audit event {number} was not emitted intact by course-registry")
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
        first["process_pid"] != second["process_pid"]
        and first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict], records: dict[tuple[str, str], dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancellation, notification, or unknown operation ran")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required course-registry operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    expected_ids = {
        scope: record["stable_id"] for scope, record in records.items()
    }
    observed_searches = {
        (event.get("title"), event.get("campus")): event for event in searches
    }
    if set(observed_searches) != set(expected_ids):
        fail("the two required exact title-and-campus searches were not run")
    for scope, stable_id in expected_ids.items():
        event = observed_searches[scope]
        if event.get("result_count") != 1 or event.get("result_ids") != [stable_id]:
            fail("both course searches must return one exact current stable ID")
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
    records_by_id = {record["stable_id"]: record for record in records.values()}
    if {event.get("stable_id") for event in retrievals} != set(records_by_id):
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in retrievals):
        fail("a complete current course record was not retrieved")
    for event in retrievals:
        record = records_by_id[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved course record was not returned completely and intact")
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


def expected_report(records: dict[tuple[str, str], dict]) -> str:
    first = records[TARGET_SCOPES[0]]
    second = records[TARGET_SCOPES[1]]
    status_relation = "same" if first["status"] == second["status"] else "different"
    date_relation = "same" if first["date"] == second["date"] else "different"
    return (
        f"- {first['title']} | ID: {first['stable_id']} | campus: "
        f"{first['campus']} | status: {first['status']} | date: {first['date']}\n"
        f"- {second['title']} | ID: {second['stable_id']} | campus: "
        f"{second['campus']} | status: {second['status']} | date: "
        f"{second['date']}\n"
        f"- Comparison | status: {status_relation} ({first['status']} vs "
        f"{second['status']}) | date: {date_relation} ({first['date']} vs "
        f"{second['date']})\n"
    )


def verify_report(records: dict[tuple[str, str], dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("course-handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected_report(records):
        fail("course-handoff.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    expected, records = expected_state()
    verify_database_state(expected)
    verify_execution(load_events(), records)
    verify_report(records)
    print(
        "PASS: grounded course comparison and genuine Pi registry execution "
        "satisfy dependency, concurrency, exact-format, and read-only constraints"
    )


if __name__ == "__main__":
    main()
