#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi course handoff seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "course-audit.jsonl"
DATABASE_PATH = ROOT / ".course_data" / "runtime" / "courses.sqlite3"
REPORT_PATH = ROOT / "course-handoff.md"
AUDIT_KEY = b"moonshiner-pi-course-audit-0192-v1"

EXPECTED_HASHES = {
    "coursectl": "28e3302ad3484f3824e4258147db4710021a8d6a4a7a4260c6539601009f789b",
    ".course_data/seed.sql": "9e4af715bf2f06056d07f8ac73f7827bf5f1538b42f1cb05f49f956cd724d53f",
    ".gitignore": "4766c9270111807ef0881ec0d65d90c3797801ce15dd0082b845712ef88263de",
}
EXPECTED_DATABASE_DIGEST = "237ef8f544c2aad1a87b5de4dbc6eedb01948a385e96eb3b84c935e161394712"
EXPECTED_SEARCHES = {
    ("Microeconomics evening seminar", "Downtown Campus"),
    ("Modern history survey", "North Campus"),
}
EXPECTED_REPORT = (
    "Microeconomics evening seminar | crs-73ad91e4 | Downtown Campus | "
    "scheduled | 2026-09-14\n"
    "Modern history survey | crs-b8402fc7 | North Campus | confirmed | "
    "2026-09-18\n"
    "Comparison | status: different (scheduled vs confirmed) | date: different "
    "(2026-09-14 vs 2026-09-18)\n"
    "No changes made.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_ROOT_ENTRIES = {
    ".course_data",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    "course-handoff.md",
    "coursectl",
}
ALLOWED_COURSE_DATA_ENTRIES = {
    "runtime",
    "runtime/courses.sqlite3",
    "runtime/courses.sqlite3-shm",
    "runtime/courses.sqlite3-wal",
    "runtime/initialize.lock",
    "seed.sql",
}
ALLOWED_PROTECTED_ENTRIES = {
    "reference_setup.py",
    "runtime",
    "runtime/course-audit.jsonl",
    "verify.py",
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
            fail(f"protected course register input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    for directory, allowed in (
        (ROOT / ".course_data", ALLOWED_COURSE_DATA_ENTRIES),
        (ROOT / ".protected", ALLOWED_PROTECTED_ENTRIES),
    ):
        nested_unexpected = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.relative_to(directory).as_posix() not in allowed
        )
        if nested_unexpected:
            fail(
                f"unexpected scratch artifact under {directory.name}: "
                + ", ".join(nested_unexpected)
            )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite course register was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, campus, status, course_date, instructor,
                       room, lifecycle
                  FROM course_records
                 ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                  FROM notifications
                 ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exception:
        fail(f"course register database is unreadable: {exception}")

    if integrity != ("ok",):
        fail("course register database integrity check failed")
    snapshot = {
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "course or notification state changed; target, draft, related, "
            "similarly named, other-campus, and archived records must stay untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine coursectl execution evidence was produced")
    events: list[dict] = []
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
            fail(f"audit event {number} was not emitted intact by coursectl")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda event: event["start_ns"])


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
        fail("an update, cancel, notify, or unknown course operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required course operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first course-data action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the later course-data action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("campus")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-campus searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both searches must resolve to exactly one current stable ID")
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
        fail("the searches were not concurrent sibling Pi Bash calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current course record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("course-handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("course-handoff.md is not the exact grounded four-line handoff")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine Pi course searches and retrievals satisfy the dependency, "
        "concurrency, grounding, scope, and read-only constraints"
    )


if __name__ == "__main__":
    main()
