#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "library-audit.jsonl"
DATABASE_PATH = ROOT / ".catalog" / "runtime" / "library.sqlite3"
REPORT_PATH = ROOT / "title_comparison.md"
AUDIT_KEY = b"moonshiner-pi-library-audit-0233-v1"

EXPECTED_HASHES = {
    "library-registry": "b5a29635355b2223fcb43aede4e8305d1d3d6e7224626793a4e8c2aefe8057ba",
    ".catalog/seed.sql": "a77fa38f6ce1f932d72ea44c19a81f4f21f074d2157f2bfaea90c63ac8f53e9c",
    "README.md": "d95bcf23c6517fe7c6901388275e17499f6ecbceedf15e48869df5268f20d078",
    ".gitignore": "c2b3a5244eb8ffdd1389df108cd08618d2c5f5f5e3c1d983ff6252e06ea41f5b",
}
EXPECTED_DATABASE_DIGEST = "0d44bff11be19b9bc5e578782b225d330928bb66355698d8f4e91486cbd6d290"
REQUESTED_LOOKUPS = (
    ("Accessible Exhibit Design", "Museum Branch"),
    ("Night Sky Field Notes", "North Branch"),
)
ALLOWED_OPERATIONS = {"search", "get"}
REQUIRED_RECORD_FIELDS = {
    "title_id",
    "title",
    "branch",
    "status",
    "date",
    "call_number",
    "collection",
    "notes",
    "lifecycle",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".catalog",
    ".reference_solution",
    "README.md",
    "library-registry",
    "title_comparison.md",
}
DIRECT_LAUNCH_TOLERANCE_NS = 500_000_000


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
            fail(f"protected library-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at sandbox root: " + ", ".join(unexpected))


def database_snapshot() -> tuple[dict, dict[str, dict]]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite library registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT title_id, title, branch, status, record_date, call_number,
                       collection_name, notes, lifecycle
                FROM title_records ORDER BY title_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, title_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"library registry database is unreadable: {exc}")

    if integrity is None or integrity[0] != "ok":
        fail("library registry database integrity check failed")
    records = [list(row) for row in rows]
    snapshot = {
        "records": records,
        "notifications": [list(row) for row in notifications],
    }
    records_by_id = {
        row[0]: {
            "title_id": row[0],
            "title": row[1],
            "branch": row[2],
            "status": row[3],
            "date": row[4],
            "call_number": row[5],
            "collection": row[6],
            "notes": row[7],
            "lifecycle": row[8],
        }
        for row in records
    }
    return snapshot, records_by_id


def verify_database_state() -> dict[str, dict]:
    snapshot, records_by_id = database_snapshot()
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "library state changed; targets, similarly named, related, archived, "
            "other title records, and notifications must remain untouched"
        )
    return records_by_id


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine library-registry execution trace was produced")
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
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("audit event IDs are not unique")
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


def launched_directly(event: dict) -> bool:
    ticks = event.get("process_start_ticks")
    if not isinstance(ticks, str) or not ticks.isdigit():
        return False
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    process_start_ns = int(ticks) * 1_000_000_000 // ticks_per_second
    launch_age_ns = event["start_ns"] - process_start_ns
    return 0 <= launch_age_ns <= DIRECT_LAUNCH_TOLERANCE_NS


def verify_execution(events: list[dict], records_by_id: dict[str, dict]) -> dict:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancel, notify, or unknown registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required library-registry operation must succeed")
    if any(not launched_directly(event) for event in events):
        fail("a registry operation was delayed behind a shell wrapper")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    searches_by_lookup = {
        (event.get("title"), event.get("branch")): event for event in searches
    }
    if set(searches_by_lookup) != set(REQUESTED_LOOKUPS):
        fail("the two required exact title-and-branch searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both title lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable title ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash calls")

    ids_by_lookup = {
        lookup: event["result_ids"][0]
        for lookup, event in searches_by_lookup.items()
    }
    for lookup, title_id in ids_by_lookup.items():
        record = records_by_id.get(title_id)
        if record is None or (record["title"], record["branch"]) != lookup:
            fail("a search result does not identify its exact requested title record")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = set(ids_by_lookup.values())
    if {event.get("title_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current title record was not retrieved")
    for event in gets:
        title_id = event.get("title_id")
        record = records_by_id.get(title_id)
        if record is None or set(record) != REQUIRED_RECORD_FIELDS:
            fail("a retrieval did not resolve to a complete managed title record")
        if event.get("field_count") != len(REQUIRED_RECORD_FIELDS):
            fail("a retrieval did not return every complete-record field")
        if event.get("record_digest") != hashlib.sha256(canonical(record)).hexdigest():
            fail("a retrieval result was not the complete current managed record")
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
    return ids_by_lookup


def verify_report(records_by_id: dict[str, dict], ids_by_lookup: dict) -> None:
    ordered = [records_by_id[ids_by_lookup[lookup]] for lookup in REQUESTED_LOOKUPS]
    first, second = ordered
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected = (
        f'- {first["title"]} | {first["branch"]} | ID {first["title_id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["title"]} | {second["branch"]} | ID {second["title_id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | dates {date_relation} '
        f'({first["date"]} vs {second["date"]})\n'
    )
    if not REPORT_PATH.is_file():
        fail("title_comparison.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("title_comparison.md is not the exact grounded requested-order comparison")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    records_by_id = verify_database_state()
    ids_by_lookup = verify_execution(load_events(), records_by_id)
    verify_report(records_by_id, ids_by_lookup)
    print(
        "PASS: grounded title comparison and genuine Pi registry execution satisfy "
        "every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
