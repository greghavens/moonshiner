#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi expense dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "expense-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".expenses" / "runtime" / "expenses.sqlite3"
SEED_PATH = ROOT / ".expenses" / "seed.sql"
REPORT_PATH = ROOT / "expense-review.md"

EXPECTED_HASHES = {
    "expense-registry": "8566a961cd1a5ae98553427d3400b5fd4639c0cb57cb2fab25b8414dcd38fb31",
    ".protected/audit.key": "4768d24e2aef56fc6c6418d129ddaf6259d50d67f46287266ea7b5e12dffcc2a",
    ".protected/reference_setup.py": "aa66fe25314b2dc9b88c0c9c200cfefa27ac6a80b939370b1594afa857b666a5",
    ".expenses/seed.sql": "0570f9065b470973af3a5e9197dcf3fdb2aba52398da931c4544c8f7952d2e86",
    "README.md": "270de7100f8e87d90a5ad29fdeb42fbfc0ac5e52c6e998b051b82ae330cf4c2b",
    ".gitignore": "482467f3d832d0b7a39c0f59f3f867e4bcfc8cc708aa1fe5701b8a89ccbfa73b",
}
EXPECTED_MODES = {
    "expense-registry": 0o755,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    ".expenses/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".expenses",
    ".expenses/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".expenses/runtime/expenses.sqlite3",
    ".expenses/runtime/initialize.lock",
    ".expenses/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/expense-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "expense-registry",
    "expense-review.md",
}
TARGET_SCOPES = (
    ("Denver lodging — policy summit", "Denver"),
    ("Tucson mileage — field sampling", "Tucson"),
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
            fail(f"supplied expense-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"supplied expense-registry input mode changed: {relative}")


def database_snapshot(connection: sqlite3.Connection) -> dict:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity != ("ok",):
        fail("expense registry database integrity check failed")
    schema = connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema ORDER BY type, name
        """
    ).fetchall()
    expenses = connection.execute(
        """
        SELECT stable_id, name, city, status, expense_date, amount_cents,
               currency, category, notes, lifecycle
        FROM expenses ORDER BY stable_id
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
        "expenses": [list(row) for row in expenses],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequences],
    }


def record_from_row(row: tuple) -> dict:
    record = {
        "name": row[0],
        "stable_id": row[1],
        "city": row[2],
        "amount_cents": row[5],
        "currency": row[6],
        "category": row[7],
        "notes": row[8],
        "lifecycle": row[9],
    }
    if row[3] is not None:
        record["status"] = row[3]
    if row[4] is not None:
        record["date"] = row[4]
    return record


def expected_state() -> tuple[dict, dict[tuple[str, str], dict]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        connection.commit()
        snapshot = database_snapshot(connection)
        records: dict[tuple[str, str], dict] = {}
        for name, city in TARGET_SCOPES:
            rows = connection.execute(
                """
                SELECT name, stable_id, city, status, expense_date, amount_cents,
                       currency, category, notes, lifecycle
                FROM expenses
                WHERE name = ? AND city = ? AND lifecycle = 'current'
                ORDER BY stable_id
                """,
                (name, city),
            ).fetchall()
            if len(rows) != 1:
                fail("protected seed no longer has one current target per scope")
            records[(name, city)] = record_from_row(rows[0])
        return snapshot, records
    finally:
        connection.close()


def verify_database_state(expected: dict) -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            actual = database_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expense registry database is unreadable: {exc}")
    if actual != expected:
        fail("expense or notification state changed during the read-only review")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expense-registry execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by expense-registry")
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
    if len({event.get("event_id") for event in events}) != len(events):
        fail("expense-registry execution events are not unique")
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
        fail("every required expense-registry operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    expected_ids = {
        scope: record["stable_id"] for scope, record in records.items()
    }
    observed_searches = {
        (event.get("name"), event.get("city")): event for event in searches
    }
    if set(observed_searches) != set(expected_ids):
        fail("the two required exact name-and-city searches were not run")
    for scope, stable_id in expected_ids.items():
        event = observed_searches[scope]
        if event.get("result_count") != 1 or event.get("result_ids") != [stable_id]:
            fail("both expense searches must return one exact current stable ID")
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
        fail("a complete current expense record was not retrieved")
    for event in retrievals:
        record = records_by_id[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved expense record was not returned completely and intact")
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


def report_value(record: dict, field: str) -> str:
    value = record.get(field)
    return value if isinstance(value, str) and value else "unknown"


def relation(first: str, second: str) -> str:
    if "unknown" in {first, second}:
        return "unknown"
    return "same" if first == second else "different"


def expected_report(records: dict[tuple[str, str], dict]) -> str:
    first = records[TARGET_SCOPES[0]]
    second = records[TARGET_SCOPES[1]]
    first_status = report_value(first, "status")
    second_status = report_value(second, "status")
    first_date = report_value(first, "date")
    second_date = report_value(second, "date")
    return (
        f"- {report_value(first, 'name')} | {report_value(first, 'city')} | "
        f"ID {report_value(first, 'stable_id')} | status {first_status} | "
        f"date {first_date}\n"
        f"- {report_value(second, 'name')} | {report_value(second, 'city')} | "
        f"ID {report_value(second, 'stable_id')} | status {second_status} | "
        f"date {second_date}\n"
        f"- Comparison | statuses {relation(first_status, second_status)} "
        f"({first_status} vs {second_status}) | dates "
        f"{relation(first_date, second_date)} ({first_date} vs {second_date})\n"
    )


def verify_report(records: dict[tuple[str, str], dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("expense-review.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != expected_report(records):
        fail("expense-review.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    expected, records = expected_state()
    verify_database_state(expected)
    verify_execution(load_events(), records)
    verify_report(records)
    print(
        "PASS: grounded expense comparison and genuine Pi registry execution "
        "satisfy dependency, concurrency, missing-field, and read-only constraints"
    )


if __name__ == "__main__":
    main()
