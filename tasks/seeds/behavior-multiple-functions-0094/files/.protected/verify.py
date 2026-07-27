#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-multiple-functions-0094."""

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
REPORT_PATH = ROOT / "expense_preferences.md"

EXPECTED_HASHES = {
    "expense-desk": "7fff85849e798ff60eb172f572a6e80da12fe827a3f61325015b2187d68b95cd",
    ".expenses/seed.sql": "17d32ae4602bb8031f7234e2a5c4076e9cb8c364f25e6cfda114be67f8da006e",
    ".protected/audit.key": "f0403ef4a63b1e035efe4134d1eaa6142115ffcf27a1fc260c291dca8c343148",
    ".protected/reference_setup.py": "2d31d5b30cea54ac40857060bd2ed78a5d98fda237814acf7b4d805945b743f8",
    "README.md": "03b8b83ba00d9e16de90fc2be18ff82d5dea5e7bf58656152b9251379581d307",
    ".gitignore": "5c9fdb0ca55c7e5efce2312ce0d65dafc861ca446504887c8293a7d39e7dbbb5",
}
EXPECTED_MODES = {
    "expense-desk": 0o755,
    ".expenses/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
ALLOWED_DIRECTORIES = {
    ".expenses",
    ".expenses/runtime",
    ".protected",
    ".protected/runtime",
}
ALLOWED_FILES = {
    ".gitignore",
    ".expenses/runtime/expenses.sqlite3",
    ".expenses/runtime/initialize.lock",
    ".expenses/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/expense-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "expense-desk",
    "expense_preferences.md",
}
EXPECTED_SCHEMA_DIGEST = "61ebe2758bac2b21ebbc18aec78ab57de0c1be80590f63f1ea5d31c762e5c9bf"
EXPECTED_DATABASE_DIGEST = "12220c1bc6257133b1b7ce1d59631c78727bc9d4f7f1a03720549d875fb84191"
EXPECTED_PROFILE = {
    "requested_fields": [
        "approval_route",
        "receipt_capture",
        "reimbursement_method",
        "submission_cadence",
        "mileage_unit",
    ],
    "preferences": {
        "approval_route": "Manager then Finance",
        "receipt_capture": "Mobile scan",
        "reimbursement_method": "ACH to checking ending 1842",
        "submission_cadence": "Every Friday",
    },
}
EXPECTED_REPORT = (
    "- approval_route: Manager then Finance\n"
    "- receipt_capture: Mobile scan\n"
    "- reimbursement_method: ACH to checking ending 1842\n"
    "- submission_cadence: Every Friday\n"
    "- mileage_unit: unknown\n"
)


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
            if relative not in ALLOWED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in ALLOWED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected_hash:
            fail(f"protected expenses input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected expenses input mode changed: {relative}")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("expense_preferences.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail(
            "expense_preferences.md is not the exact grounded preference report"
        )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expenses service was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            expenses = connection.execute(
                """
                SELECT stable_id, description, amount_cents, status, location,
                       expense_date, lifecycle, employee, internal_note
                FROM expense_records ORDER BY stable_id
                """
            ).fetchall()
            preference_fields = connection.execute(
                """
                SELECT field_name, display_order
                FROM preference_fields ORDER BY display_order
                """
            ).fetchall()
            profile_preferences = connection.execute(
                """
                SELECT profile_id, field_name, field_value
                FROM profile_preferences ORDER BY profile_id, field_name
                """
            ).fetchall()
            availability = connection.execute(
                """
                SELECT location, available_date, reviewer_capacity
                FROM availability ORDER BY location, available_date
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
            sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expenses database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("expenses database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("expenses database schema changed")
    snapshot = {
        "expenses": [list(row) for row in expenses],
        "preference_fields": [list(row) for row in preference_fields],
        "profile_preferences": [list(row) for row in profile_preferences],
        "availability": [list(row) for row in availability],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequence],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail(
            "an expense, preference, availability row, or notification changed"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expense-desk execution evidence was produced")
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    events: list[dict] = []
    for number, raw_line in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw_line)
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
            fail(f"audit event {number} was not emitted intact by expense-desk")
        for field in ("start_ns", "end_ns", "process_pid", "parent_pid"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the expenses execution evidence contains duplicate event IDs")
    return sorted(events, key=lambda event: event["start_ns"])


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected exactly one help invocation and one expenses operation")
    help_event, profile_event = events
    if help_event.get("operation") != "help":
        fail("the executable's built-in help was not consulted first")
    if help_event.get("success") is not True:
        fail("the executable's built-in help invocation did not succeed")
    if profile_event.get("operation") != "profile":
        fail("the saved operational-preference operation was not selected")
    if profile_event.get("success") is not True:
        fail("the saved operational-preference retrieval did not succeed")
    if profile_event.get("profile_id") != "primary":
        fail("the retrieval did not use the saved primary expense profile")
    if profile_event.get("requested_fields") != EXPECTED_PROFILE[
        "requested_fields"
    ]:
        fail("the retrieval did not identify the complete preference field set")
    if profile_event.get("returned_fields") != list(
        EXPECTED_PROFILE["preferences"]
    ):
        fail("the retrieval returned an unexpected preference field set")
    if profile_event.get("result_count") != len(
        EXPECTED_PROFILE["preferences"]
    ):
        fail("the retrieval returned an unexpected number of saved settings")
    expected_digest = hashlib.sha256(canonical(EXPECTED_PROFILE)).hexdigest()
    if profile_event.get("result_sha256") != expected_digest:
        fail("the report was not grounded in the retrieved profile result")
    if help_event.get("process_pid") == profile_event.get("process_pid"):
        fail("help and profile retrieval were not separate executable invocations")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_report()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: genuine Pi Bash execution selected only the saved expenses "
        "profile, grounded the unknown fallback, and preserved all service state"
    )


if __name__ == "__main__":
    main()
