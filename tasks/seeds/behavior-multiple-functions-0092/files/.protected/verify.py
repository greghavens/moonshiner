#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-multiple-functions-0092."""

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
REPORT_PATH = ROOT / "course_report.md"

EXPECTED_HASHES = {
    "course-registry": "65803471e594187aa4d610b7fc6528dcb506d718635bb2bb226b0f10e03c3d59",
    ".courses/seed.sql": "54d60b53e445e1ee7c9e2c4ca688b4cde2d983bbac2b8b34a0e072472c08e145",
    ".protected/audit.key": "c431ac17eda48e0a324a9b3003b6c831270f64c8e2f83558dbda51b3be2c0470",
    ".protected/reference_setup.py": "837f88a132c3099ab6e123f4636305b34a7c74519d69567b05e9f94f97e0ddf7",
    "README.md": "3e1dd79ed07529bd844b04413e8b128af295c8e3e9bf3f1b7bc17e8ffda6ffa0",
    ".gitignore": "5c8a1b5fb17df7dd13898c9931639347f0c64385816fc4111fdda740cffce4ac",
}
EXPECTED_MODES = {
    "course-registry": 0o755,
    ".courses/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
ALLOWED_DIRECTORIES = {
    ".courses",
    ".courses/runtime",
    ".protected",
    ".protected/runtime",
}
ALLOWED_FILES = {
    ".gitignore",
    ".courses/runtime/courses.sqlite3",
    ".courses/runtime/initialize.lock",
    ".courses/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/course-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "course-registry",
    "course_report.md",
}
EXPECTED_SCHEMA_DIGEST = "916b0c5b709ff6d6eaad80f6f9767cd9eee588e04bbc57ef3c04920913fe804a"
EXPECTED_DATABASE_DIGEST = "66fd9e94d51a5ffbfd8f890fe911daba3ba07348fe336ec5e5a10740df3ffe83"
EXPECTED_COURSE = {
    "id": "edu-192",
    "title": "Urban Ecology Field Methods",
    "status": "enrollment-open",
    "location": "West Campus — Alder Hall",
    "start_date": "2026-09-03",
}
EXPECTED_REPORT = (
    "- ID: edu-192\n"
    "- Status for edu-192: enrollment-open\n"
    "- Location for edu-192: West Campus — Alder Hall\n"
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
            fail(f"protected education input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected education input mode changed: {relative}")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("course_report.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("course_report.md is not the exact grounded three-bullet report")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite education registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            courses = connection.execute(
                """
                SELECT stable_id, title, status, location, start_date,
                       lifecycle, instructor, capacity, internal_note
                FROM courses ORDER BY stable_id
                """
            ).fetchall()
            availability = connection.execute(
                """
                SELECT location, session_date, open_seats
                FROM availability ORDER BY location, session_date
                """
            ).fetchall()
            profiles = connection.execute(
                """
                SELECT profile_id, default_location, notification_channel
                FROM profiles ORDER BY profile_id
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
        fail(f"education registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("education registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("education registry schema changed")
    snapshot = {
        "courses": [list(row) for row in courses],
        "availability": [list(row) for row in availability],
        "profiles": [list(row) for row in profiles],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequence],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("a course, availability row, profile, or notification changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine course-registry execution evidence was produced")
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
            fail(f"audit event {number} was not emitted intact by course-registry")
        for field in ("start_ns", "end_ns", "process_pid", "parent_pid"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the education execution evidence contains duplicate event IDs")
    return sorted(events, key=lambda event: event["start_ns"])


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expected exactly one help invocation and one education operation")
    help_event, open_event = events
    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the executable's built-in help was not consulted first")
    if open_event.get("operation") != "open":
        fail("the single-course stable-ID retrieval operation was not selected")
    if open_event.get("success") is not True:
        fail("the requested stable-ID course retrieval did not succeed")
    if open_event.get("course_id") != "edu-192":
        fail("the retrieval did not use the exact requested stable ID")
    if open_event.get("result_count") != 1:
        fail("the stable-ID retrieval did not return exactly one course")
    if open_event.get("result_ids") != ["edu-192"]:
        fail("the stable-ID retrieval returned an out-of-scope course")
    expected_digest = hashlib.sha256(canonical([EXPECTED_COURSE])).hexdigest()
    if open_event.get("result_sha256") != expected_digest:
        fail("the report was not grounded in the retrieved course")
    if open_event.get("returned_fields") != sorted(EXPECTED_COURSE):
        fail("the retrieval did not return the complete public course")
    if help_event.get("process_pid") == open_event.get("process_pid"):
        fail("help and course retrieval were not separate executable invocations")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_report()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: genuine Pi Bash execution selected one exact stable-ID course read, "
        "grounded the three bullets, and preserved all education state"
    )


if __name__ == "__main__":
    main()
