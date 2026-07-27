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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "ledger-audit.jsonl"
DATABASE_PATH = ROOT / ".expense_data" / "runtime" / "ledger.sqlite3"
REPORT_PATH = ROOT / "expense-review.md"
AUDIT_KEY = b"moonshiner-pi-expense-audit-0194-v1"

EXPECTED_HASHES = {
    "expense-ledger": "49987804cc3df2fba9444527d7cd9395ba2467328c3c2e1338b10b2a4af6fc45",
    ".harness/reference_setup.py": "1aa4289a0d6034222b3d514a16807820f39bafbe13edcc7d091b27168329bb9b",
    ".expense_data/seed.sql": "6a0446710fc0278e474355bdd4650442bc04a5a4f166a7a2963a2c7efb6c3f45",
    "README.md": "a8ef98aa5ed5601726edc7ef564d105eed21433e041ddb6870fd429461e64e13",
    ".gitignore": "f79eb72af5e7e77fc1e6b466aad8798574a1a20823f494202c6dd5a2c3370862",
}
EXPECTED_MODES = {
    "expense-ledger": 0o755,
    ".harness/reference_setup.py": 0o755,
    ".expense_data/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".expense_data",
    ".expense_data/runtime",
    ".harness",
    ".harness/runtime",
}
EXPECTED_FILES = {
    ".expense_data/runtime/initialize.lock",
    ".expense_data/runtime/ledger.sqlite3",
    ".expense_data/seed.sql",
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/ledger-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "expense-ledger",
    "expense-review.md",
}
EXPECTED_SCHEMA_DIGEST = "10ee411c0d17d35fce52e281ddf7446b366d25b0e5f221945a7c2c08c51ba717"
EXPECTED_DATABASE_DIGEST = "9e1965c5d3b2077d8818247ad5fef14d69ca8d775139a0a473dfe163e26c97be"
EXPECTED_SEARCH_IDS = {
    ("Chicago rail fare — outreach trip", "Chicago"): "exp-4187",
    ("Boston team lunch — budget workshop", "Boston"): "exp-7724",
}
EXPECTED_REPORT = (
    "Chicago rail fare — outreach trip in Chicago (exp-4187) has status "
    "approved and date 2026-07-18.\n"
    "Boston team lunch — budget workshop in Boston (exp-7724) has status "
    "pending-review and date 2026-07-19.\n"
    "Comparison: statuses differ (approved versus pending-review), and dates "
    "differ (2026-07-18 versus 2026-07-19).\n"
)
EXPECTED_RECORDS = {
    "exp-4187": {
        "description": "Chicago rail fare — outreach trip",
        "stable_id": "exp-4187",
        "city": "Chicago",
        "status": "approved",
        "date": "2026-07-18",
        "amount_cents": 1845,
        "submitted_by": "Mina Patel",
        "cost_center": "Community Outreach",
        "lifecycle": "current",
    },
    "exp-7724": {
        "description": "Boston team lunch — budget workshop",
        "stable_id": "exp-7724",
        "city": "Boston",
        "status": "pending-review",
        "date": "2026-07-19",
        "amount_cents": 12680,
        "submitted_by": "Noah Williams",
        "cost_center": "Finance Enablement",
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
            fail(f"protected expense-ledger input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected expense-ledger input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            expenses = connection.execute(
                """
                SELECT stable_id, description, city, status, expense_date,
                       amount_cents, submitted_by, cost_center, lifecycle
                FROM expenses ORDER BY stable_id
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
        fail(f"expense ledger database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("expense ledger database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("expense ledger schema changed")
    snapshot = {
        "expenses": [list(row) for row in expenses],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "expense ledger state changed; requested, distractor, archived, "
            "and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expense-ledger execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by expense-ledger")
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
        fail("every required expense-ledger operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first ledger action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next ledger action must contain only both retrievals")

    observed_searches = {
        (event.get("description"), event.get("city")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact description-and-city searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both expense lookups must resolve to one current stable ID")
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
        fail("a complete current expense record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved expense record was not returned completely and intact")
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
        fail("expense-review.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("expense-review.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded expense comparison and genuine Pi ledger execution "
        "satisfy dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
