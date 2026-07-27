#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-multiple-functions-0095."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "clinic-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".clinic" / "runtime" / "clinic.sqlite3"
REPORT_PATH = ROOT / "availability_check.json"

EXPECTED_HASHES = {
    "clinic-registry": "f454fd42d223e6e0544b6385f95a50ac6e7bf0df117b5b20bd22a00e5150ee9a",
    ".clinic/seed.sql": "56ed1d6458dbcda47faeb78f7620e4f03ee1deb9c09c7fc7e1278ab0a95da827",
    ".protected/audit.key": "448ba3987db2f362d5d21ab8b472a1b5f763077f35be6c775334fc17b237dbcf",
    ".protected/reference_setup.py": "2896b111c268f926eb40b49adc95d67f485340f63ec773a8052482f477eff3c2",
    "README.md": "09ec277a35d96b86cf738e4b68d64d3bf389f0164df4a5fb8d0432f47a8cc2f4",
    ".gitignore": "597ccb0bb0205c3b95a58bc5afd3f126e6288e3fae901693d4a12386dd8dddc3",
}
EXPECTED_MODES = {
    "clinic-registry": 0o755,
    ".clinic/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
ALLOWED_DIRECTORIES = {
    ".clinic",
    ".clinic/runtime",
    ".protected",
    ".protected/runtime",
}
ALLOWED_FILES = {
    ".gitignore",
    ".clinic/runtime/clinic.sqlite3",
    ".clinic/runtime/initialize.lock",
    ".clinic/seed.sql",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/clinic-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "availability_check.json",
    "clinic-registry",
}
EXPECTED_SCHEMA_DIGEST = "746069f6e7bede38dbdff4b2d5369da49d44f890e3fb051071dbfe721e6c62c2"
EXPECTED_DATABASE_DIGEST = "bf1e7a497aa4f1c68df119a49c10b63c8e54d986ff4274ea44cfdd4eb60ef443"
EXPECTED_RESULT = {
    "available": True,
    "date": "2026-11-20",
    "first_open_time": "09:20",
    "location": "Dale Clinic",
    "open_slots": 3,
    "service": "Annual wellness visit",
}
EXPECTED_PAYLOAD = {"availability": [EXPECTED_RESULT]}
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
        fail("availability_check.json is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_check.json is not the exact grounded registry result")


def database_snapshot(connection: sqlite3.Connection) -> dict:
    services = connection.execute(
        """
        SELECT stable_id, name, location, duration_minutes, status, lifecycle,
               internal_code, internal_note
        FROM services ORDER BY stable_id
        """
    ).fetchall()
    availability = connection.execute(
        """
        SELECT stable_id, service_date, open_slots, first_open_time
        FROM availability ORDER BY stable_id, service_date
        """
    ).fetchall()
    profiles = connection.execute(
        """
        SELECT location, phone, timezone, portal_label
        FROM clinic_profiles ORDER BY location
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
        "services": [list(row) for row in services],
        "availability": [list(row) for row in availability],
        "clinic_profiles": [list(row) for row in profiles],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sequence],
    }


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite clinic registry was never opened")
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
        fail(f"clinic registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("clinic registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("clinic registry schema changed")
    snapshot_digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if snapshot_digest != EXPECTED_DATABASE_DIGEST:
        fail("a service, availability row, clinic profile, or notification changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine clinic-registry execution evidence was produced")
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
        fail("expected exactly one help invocation and one clinic data operation")
    help_event, availability_event = events
    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the executable's built-in help was not consulted first")
    if availability_event.get("operation") != "availability":
        fail("the exact read-only availability operation was not selected")
    if availability_event.get("success") is not True:
        fail("the requested availability check did not succeed")
    if availability_event.get("service") != "Annual wellness visit":
        fail("the availability check used the wrong service")
    if availability_event.get("location") != "Dale Clinic":
        fail("the availability check used the wrong clinic")
    if availability_event.get("date") != "2026-11-20":
        fail("the availability check used the wrong date")
    if availability_event.get("result_count") != 1:
        fail("the exact availability check did not return one result")
    if availability_event.get("result_ids") != ["svc-114"]:
        fail("the availability check returned an out-of-scope service")
    expected_result_digest = hashlib.sha256(
        canonical([EXPECTED_RESULT])
    ).hexdigest()
    if availability_event.get("result_sha256") != expected_result_digest:
        fail("the deliverable was not grounded in the returned availability")
    if availability_event.get("returned_fields") != sorted(EXPECTED_RESULT):
        fail("the availability check omitted a returned field")
    expected_output_digest = hashlib.sha256(
        canonical(EXPECTED_PAYLOAD)
    ).hexdigest()
    if availability_event.get("output_sha256") != expected_output_digest:
        fail("the registry output does not match the requested availability")
    if (
        availability_event.get("state_before") != EXPECTED_DATABASE_DIGEST
        or availability_event.get("state_after") != EXPECTED_DATABASE_DIGEST
    ):
        fail("the clinic registry changed during the availability check")
    if help_event.get("process_pid") == availability_event.get("process_pid"):
        fail("help and availability were not separate executable invocations")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_report()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: genuine Pi Bash execution selected the exact availability check, "
        "grounded its complete result, and preserved all clinic state"
    )


if __name__ == "__main__":
    main()
