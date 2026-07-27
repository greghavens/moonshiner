#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-multiple-functions-0091."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "candidate-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".registry" / "runtime" / "candidates.sqlite3"
REPORT_PATH = ROOT / "candidate_record.json"

EXPECTED_HASHES = {
    "candidate-registry": "cac105ba861154f633ffed5b9479b7f265f2f1b2d4d7bcf16f77e30e67365e27",
    ".registry/seed.sql": "24612b2ed746f8de05f1d8779a0b2a4d221624e7259875ec958dd0a0d7ea32ef",
    ".protected/audit.key": "c5633118b5bc0ca1bebdf7f1758cc56c9aaa6f7db33198beb919a39e2dfb3997",
    ".protected/reference_setup.py": "f30b0bd3ec2a6af8dabaa0c20cdaf5555c27b4741263ba5d8a4c9471159a64e3",
    "README.md": "622b191cbe3d4e9aba4e22ef9327280e1256e80ba221a0a63d0bdf08c9bab2ac",
    ".gitignore": "745e663ac61cb1240d4cd248a41ee175e6e26d1163a1733a4897dc075704d168",
}
EXPECTED_MODES = {
    "candidate-registry": 0o755,
    ".registry/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
ALLOWED_DIRECTORIES = {
    ".registry",
    ".registry/runtime",
    ".protected",
    ".protected/runtime",
}
ALLOWED_FILES = {
    ".gitignore",
    ".registry/runtime/candidates.sqlite3",
    ".registry/runtime/initialize.lock",
    ".registry/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/candidate-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "candidate-registry",
    "candidate_record.json",
}
EXPECTED_SCHEMA_DIGEST = "70db6d0ff6f8b9d4270b0f11dcd061a7cecd6b50033f7086299d7f6e99f65a83"
EXPECTED_DATABASE_DIGEST = "1b2fade18f685342349a94980376fe7637c1d3e75690a1b92a5b4cdd14d23850"
EXPECTED_CANDIDATE = {
    "id": "cand-104",
    "name": "Morgan Lee",
    "department": "Research",
    "role": "Senior Research Analyst",
    "status": "interviewing",
    "location": "Boulder, CO",
    "interview_date": "2026-08-12",
}
EXPECTED_PAYLOAD = {"matches": [EXPECTED_CANDIDATE]}
EXPECTED_REPORT = json.dumps(EXPECTED_PAYLOAD, sort_keys=True) + "\n"


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
            fail(f"protected registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected registry input mode changed: {relative}")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("candidate_record.json is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("candidate_record.json is not the exact grounded registry result")


def database_snapshot(connection: sqlite3.Connection) -> dict:
    candidates = connection.execute(
        """
        SELECT stable_id, name, department, role, status, location,
               interview_date, lifecycle, private_email, internal_note
        FROM candidates ORDER BY stable_id
        """
    ).fetchall()
    profiles = connection.execute(
        """
        SELECT stable_id, preferred_channel, timezone, portfolio_status
        FROM profiles ORDER BY stable_id
        """
    ).fetchall()
    availability = connection.execute(
        """
        SELECT stable_id, available_date, open_slots
        FROM availability ORDER BY stable_id, available_date
        """
    ).fetchall()
    notifications = connection.execute(
        """
        SELECT notification_id, stable_id, message
        FROM notifications ORDER BY notification_id
        """
    ).fetchall()
    sequence = connection.execute(
        "SELECT name, seq FROM sqlite_sequence ORDER BY name"
    ).fetchall()
    return {
        "candidates": [list(row) for row in candidates],
        "profiles": [list(row) for row in profiles],
        "availability": [list(row) for row in availability],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequence],
    }


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite recruiting registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema ORDER BY type, name
                """
            ).fetchall()
            snapshot = database_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"recruiting registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("recruiting registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("recruiting registry schema changed")
    snapshot_digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if snapshot_digest != EXPECTED_DATABASE_DIGEST:
        fail("a candidate, profile, availability row, or notification changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine candidate-registry execution evidence was produced")
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
            fail(f"audit event {number} was not emitted intact by the registry")
        for field in ("start_ns", "end_ns", "process_pid", "parent_pid"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the registry execution evidence contains duplicate event IDs")
    return sorted(events, key=lambda event: event["start_ns"])


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected exactly one help invocation and one registry operation")
    help_event, search_event = events
    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the executable's built-in help was not consulted first")
    if search_event.get("operation") != "search":
        fail("the exact structured candidate search operation was not selected")
    if search_event.get("success") is not True:
        fail("the requested exact candidate search did not succeed")
    if search_event.get("name") != "Morgan Lee":
        fail("the search did not use the exact requested candidate name")
    if search_event.get("department") != "Research":
        fail("the search did not use the exact requested department")
    if search_event.get("result_count") != 1:
        fail("the exact search did not return exactly one candidate")
    if search_event.get("result_ids") != ["cand-104"]:
        fail("the exact search returned an out-of-scope candidate")
    expected_result_digest = hashlib.sha256(
        canonical([EXPECTED_CANDIDATE])
    ).hexdigest()
    if search_event.get("result_sha256") != expected_result_digest:
        fail("the deliverable was not grounded in the returned candidate")
    if search_event.get("returned_fields") != sorted(EXPECTED_CANDIDATE):
        fail("the search did not return the complete public candidate record")
    expected_output_digest = hashlib.sha256(
        canonical(EXPECTED_PAYLOAD)
    ).hexdigest()
    if search_event.get("output_sha256") != expected_output_digest:
        fail("the registry output does not match the requested exact result")
    if (
        search_event.get("state_before") != EXPECTED_DATABASE_DIGEST
        or search_event.get("state_after") != EXPECTED_DATABASE_DIGEST
    ):
        fail("the recruiting registry changed during the search")
    if help_event.get("process_pid") == search_event.get("process_pid"):
        fail("help and candidate search were not separate executable invocations")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_report()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: genuine Pi Bash execution selected one exact candidate search, "
        "grounded the complete record, and preserved all recruiting state"
    )


if __name__ == "__main__":
    main()
