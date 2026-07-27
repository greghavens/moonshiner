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
AUDIT_PATH = ROOT / ".protected" / "runtime" / "facilities-audit.jsonl"
DATABASE_PATH = ROOT / ".facilities" / "runtime" / "facilities.sqlite3"
REPORT_PATH = ROOT / "facilities_check.md"
AUDIT_KEY = b"moonshiner-pi-facilities-audit-0237-v1"

EXPECTED_HASHES = {
    "facilities-registry": "baf25b827f138d287e11690ee179253224fd3f0f9c17444ca8985f427778adae",
    ".facilities/seed.sql": "2af5b54af60d7e8aeaca50473af6e2f9e28d80843e5f5c2b0c250042a8a48d41",
    "README.md": "1d2d899ed3ab1db12670ecf2dda4456aea9c728972f79a8643518b19d8eba0cf",
    ".gitignore": "d305ff7a23d892b20641a077484f71f1d23efde71aa37f93ff02fa54fba0efc7",
    ".protected/reference_setup.py": "116a68cda5b8e2871349da09cc2099864e0d82c4342fd96c231bd1ef1742c403",
}
EXPECTED_DATABASE_DIGEST = "99de2a3ef857d09de2c8aede49289136458b164916b74d237085b03635aca39c"
REQUESTED_LOOKUPS = {
    ("Clinic air filter replacement", "Health Center"),
    ("Museum gallery repainting", "Arts Center"),
}
ALLOWED_OPERATIONS = {"search", "get"}
REQUIRED_RECORD_FIELDS = {
    "request_id",
    "name",
    "location",
    "status",
    "date",
    "category",
    "priority",
    "assigned_team",
    "notes",
    "lifecycle",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".facilities",
    ".protected",
    "README.md",
    "facilities-registry",
    "facilities_check.md",
}
ALLOWED_DIRECTORY_ENTRIES = {
    ".facilities": {"runtime", "seed.sql"},
    ".facilities/runtime": {"facilities.sqlite3", "initialize.lock"},
    ".protected": {"reference_setup.py", "runtime", "verify.py"},
    ".protected/runtime": {"facilities-audit.jsonl"},
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
            fail(f"protected facilities-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at sandbox root: " + ", ".join(unexpected))
    for relative, allowed in ALLOWED_DIRECTORY_ENTRIES.items():
        directory = ROOT / relative
        if not directory.exists():
            continue
        if not directory.is_dir():
            fail(f"managed sandbox directory was replaced: {relative}")
        unexpected = sorted(
            path.name for path in directory.iterdir() if path.name not in allowed
        )
        if unexpected:
            fail(
                f"unexpected scratch artifact under {relative}: "
                + ", ".join(unexpected)
            )


def database_snapshot() -> tuple[dict, dict[str, dict]]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite facilities registry was never opened")
    initialize_lock = DATABASE_PATH.parent / "initialize.lock"
    if not initialize_lock.is_file() or initialize_lock.stat().st_size != 0:
        fail("the facilities initialization lock was altered")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT request_id, name, location, status, record_date, category,
                       priority, assigned_team, notes, lifecycle
                FROM facility_requests ORDER BY request_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, request_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"facilities registry database is unreadable: {exc}")

    if integrity is None or integrity[0] != "ok":
        fail("facilities registry database integrity check failed")
    records = [list(row) for row in rows]
    snapshot = {
        "records": records,
        "notifications": [list(row) for row in notifications],
    }
    records_by_id = {
        row[0]: {
            "request_id": row[0],
            "name": row[1],
            "location": row[2],
            "status": row[3],
            "date": row[4],
            "category": row[5],
            "priority": row[6],
            "assigned_team": row[7],
            "notes": row[8],
            "lifecycle": row[9],
        }
        for row in records
    }
    return snapshot, records_by_id


def verify_database_state() -> dict[str, dict]:
    snapshot, records_by_id = database_snapshot()
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "facilities state changed; targets, similarly named, related, "
            "archived, other records, and notifications must remain untouched"
        )
    return records_by_id


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine facilities-registry execution trace was produced")
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
        fail("every required facilities-registry operation must succeed")
    if any(
        event.get("report_existed_at_start") is not False
        or event.get("report_existed_at_end") is not False
        for event in events
    ):
        fail("facilities_check.md was created before both retrievals completed")
    if any(not launched_directly(event) for event in events):
        fail("a registry operation was delayed behind a shell wrapper")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    searches_by_lookup = {
        (event.get("name"), event.get("location")): event for event in searches
    }
    if set(searches_by_lookup) != REQUESTED_LOOKUPS:
        fail("the two required exact name-and-location searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both facilities lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable request ID")
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
    for lookup, request_id in ids_by_lookup.items():
        record = records_by_id.get(request_id)
        if record is None or (record["name"], record["location"]) != lookup:
            fail("a search result does not identify its exact requested record")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = set(ids_by_lookup.values())
    if {event.get("request_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current facilities record was not retrieved")
    for event in gets:
        request_id = event.get("request_id")
        record = records_by_id.get(request_id)
        if record is None or set(record) != REQUIRED_RECORD_FIELDS:
            fail("a retrieval did not resolve to a complete managed facilities record")
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
    records = [records_by_id[request_id] for request_id in ids_by_lookup.values()]
    ordered = sorted(records, key=lambda record: record["name"])
    first, second = ordered
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    expected = (
        f'- {first["name"]} | {first["location"]} | ID {first["request_id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["name"]} | {second["location"]} | ID {second["request_id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | dates {date_relation} '
        f'({first["date"]} vs {second["date"]})\n'
    )
    if not REPORT_PATH.is_file():
        fail("facilities_check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail(
            "facilities_check.md is not the exact grounded alphabetical comparison"
        )


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    records_by_id = verify_database_state()
    ids_by_lookup = verify_execution(load_events(), records_by_id)
    verify_report(records_by_id, ids_by_lookup)
    print(
        "PASS: grounded facilities comparison and genuine Pi registry execution "
        "satisfy every dependency, concurrency, ordering, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
