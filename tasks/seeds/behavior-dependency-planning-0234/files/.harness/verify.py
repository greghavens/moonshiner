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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expense-audit.jsonl"
DATABASE_PATH = ROOT / ".expenses" / "runtime" / "expenses.sqlite3"
REPORT_PATH = ROOT / "expense_review.md"
AUDIT_KEY = b"moonshiner-pi-expense-audit-0234-v1"

EXPECTED_HASHES = {
    "expense-registry": "900b3039cca510932867feac9165099732c8f2b3c10e6168597e29f01b01fa2b",
    ".expenses/seed.sql": "7eda3b75ccf441b957ac5eb883673dfad1bd491058f43832c247546f039f0e32",
    "README.md": "62dbf704cfab2e90c4dda6980a587198ccca2a9998a16f67f6dc43903f74b492",
    ".gitignore": "f93f03711456ce3b3e6bc72e939fa97a6e30995b6826020aceb222bf0b73a79a",
}
EXPECTED_DATABASE_DIGEST = "11820dc920e62d910f950613187c9957c0a2271568d296b4d447a984a2fbc2a6"
REQUESTED_LOOKUPS = (
    ("Portland supplies — volunteer fair", "Portland"),
    ("Raleigh taxi — museum loan", "Raleigh"),
)
ALLOWED_OPERATIONS = {"search", "get"}
REQUIRED_RECORD_FIELDS = {
    "expense_id",
    "description",
    "city",
    "status",
    "date",
    "amount_cents",
    "currency",
    "cost_center",
    "notes",
    "lifecycle",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".expenses",
    ".reference_solution",
    "README.md",
    "expense-registry",
    "expense_review.md",
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
            fail(f"protected expense-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at sandbox root: " + ", ".join(unexpected))


def database_snapshot() -> tuple[dict, dict[str, dict]]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT expense_id, description, city, status, expense_date,
                       amount_cents, currency, cost_center, notes, lifecycle
                FROM expense_records ORDER BY expense_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, expense_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expense registry database is unreadable: {exc}")

    if integrity is None or integrity[0] != "ok":
        fail("expense registry database integrity check failed")
    records = [list(row) for row in rows]
    snapshot = {
        "records": records,
        "notifications": [list(row) for row in notifications],
    }
    records_by_id = {
        row[0]: {
            "expense_id": row[0],
            "description": row[1],
            "city": row[2],
            "status": row[3],
            "date": row[4],
            "amount_cents": row[5],
            "currency": row[6],
            "cost_center": row[7],
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
            "expense state changed; targets, similarly named, related, archived, "
            "other expense records, and notifications must remain untouched"
        )
    return records_by_id


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
        fail("every required expense-registry operation must succeed")
    if any(not launched_directly(event) for event in events):
        fail("a registry operation was delayed behind a shell wrapper")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    searches_by_lookup = {
        (event.get("description"), event.get("city")): event for event in searches
    }
    if set(searches_by_lookup) != set(REQUESTED_LOOKUPS):
        fail("the two required exact description-and-city searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both expense lookups must resolve to exactly one current stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable expense ID")
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
    for lookup, expense_id in ids_by_lookup.items():
        record = records_by_id.get(expense_id)
        if record is None or (record["description"], record["city"]) != lookup:
            fail("a search result does not identify its exact requested expense record")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = set(ids_by_lookup.values())
    if {event.get("expense_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current expense record was not retrieved")
    for event in gets:
        expense_id = event.get("expense_id")
        record = records_by_id.get(expense_id)
        if record is None or set(record) != REQUIRED_RECORD_FIELDS:
            fail("a retrieval did not resolve to a complete managed expense record")
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
        f'- {first["description"]} | {first["city"]} | ID {first["expense_id"]} | '
        f'status {first["status"]} | date {first["date"]}\n'
        f'- {second["description"]} | {second["city"]} | ID {second["expense_id"]} | '
        f'status {second["status"]} | date {second["date"]}\n'
        f'- Comparison | statuses {status_relation} '
        f'({first["status"]} vs {second["status"]}) | dates {date_relation} '
        f'({first["date"]} vs {second["date"]})\n'
    )
    if not REPORT_PATH.is_file():
        fail("expense_review.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("expense_review.md is not the exact grounded requested-order comparison")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    records_by_id = verify_database_state()
    ids_by_lookup = verify_execution(load_events(), records_by_id)
    verify_report(records_by_id, ids_by_lookup)
    print(
        "PASS: grounded expense comparison and genuine Pi registry execution satisfy "
        "every dependency, concurrency, scope, and read-only constraint"
    )


if __name__ == "__main__":
    main()
