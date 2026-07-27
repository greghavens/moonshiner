#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "meeting-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".meetings" / "runtime" / "meetings.sqlite3"
REPORT_PATH = ROOT / "meeting-check.md"

EXPECTED_HASHES = {
    "meeting-registry": "27778510aa29d86ad102755de78766e4f167d057c60f17af94ff1276640e41ba",
    ".protected/audit.key": "4b4ec979325f2cf537ac063547788d178ce47f34fafdb343c3a1c8d1af00b0ab",
    ".protected/reference_setup.py": "b4eff60f63209cc5c0dbc056d65a4c59e36d1169dbc4efba115f753c4548d0e3",
    ".meetings/seed.sql": "8de387b52178d7dd9c5f8ebc4f2db29e28de0bfc76d1c3870b612c3a0773fbec",
    "README.md": "e4a04eec6aae3df8a8f08684adab27f269b358eb89fcedb859a318e3118dc0f7",
    ".gitignore": "9d589d5dbb18e590a76593d76d6aeec494199b30213c0b039cabba51add1ea52",
}
EXPECTED_MODES = {
    "meeting-registry": 0o755,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    ".meetings/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".meetings",
    ".meetings/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".meetings/runtime/initialize.lock",
    ".meetings/runtime/meetings.sqlite3",
    ".meetings/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/meeting-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "meeting-check.md",
    "meeting-registry",
}
EXPECTED_SCHEMA_DIGEST = "1f6ed93dd62d9b3031d99af5d6e842a5bb8933526fb711756cc6ce5ba267da4d"
EXPECTED_DATABASE_DIGEST = "953498ee1569343271a120414d1acc19bc552e23939b70b833ac3db57eb4c49f"
EXPECTED_SEARCH_IDS = {
    ("Accessibility Council Check-in", "Denver"): "mtg-421",
    ("Grant Budget Review", "Chicago"): "mtg-884",
}
EXPECTED_REPORT = (
    "Accessibility Council Check-in in Denver (mtg-421) has status confirmed "
    "and date 2026-09-03.\n"
    "Grant Budget Review in Chicago (mtg-884) has status scheduled and date "
    "2026-09-10.\n"
    "Comparison: statuses differ (confirmed versus scheduled), and dates "
    "differ (2026-09-03 versus 2026-09-10).\n"
)
EXPECTED_RECORDS = {
    "mtg-421": {
        "name": "Accessibility Council Check-in",
        "stable_id": "mtg-421",
        "city": "Denver",
        "status": "confirmed",
        "date": "2026-09-03",
        "venue": "Municipal Center 4B",
        "coordinator": "Mara Chen",
        "details": "Review public-building access items",
        "lifecycle": "current",
    },
    "mtg-884": {
        "name": "Grant Budget Review",
        "stable_id": "mtg-884",
        "city": "Chicago",
        "status": "scheduled",
        "date": "2026-09-10",
        "venue": "Grant Office 12",
        "coordinator": "Nadia Brooks",
        "details": "Review program allocation worksheet",
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
            fail(f"protected meeting-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected meeting-registry input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite meeting registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            meetings = connection.execute(
                """
                SELECT stable_id, name, city, status, meeting_date,
                       venue, coordinator, details, lifecycle
                FROM meetings ORDER BY stable_id
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
        fail(f"meeting registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("meeting registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("meeting registry schema changed")
    snapshot = {
        "meetings": [list(row) for row in meetings],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "meeting state changed; requested, distractor, archived, and "
            "notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine meeting-registry execution trace was produced")
    events = []
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
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
        expected = hmac.new(audit_key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by meeting-registry")
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


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required meeting-registry operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("city")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact name-and-city searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both meeting lookups must resolve to exactly one current stable ID")
        if event.get("result_ids") != [expected_id]:
            fail("a search did not return its one protected current stable ID")
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
    returned_ids = set(EXPECTED_SEARCH_IDS.values())
    if {event.get("stable_id") for event in retrievals} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in retrievals):
        fail("a complete current meeting record was not retrieved")
    for event in retrievals:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved meeting record was not returned completely and intact")
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
        fail("meeting-check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("meeting-check.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded meeting comparison and genuine Pi registry execution "
        "satisfy dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
