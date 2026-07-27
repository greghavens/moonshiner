#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi CRM dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "crm-audit.jsonl"
DATABASE_PATH = ROOT / ".crm" / "runtime" / "crm.sqlite3"
AUDIT_KEY = b"moonshiner-pi-crm-audit-0149-v1"

EXPECTED_HASHES = {
    "crmctl": "1474d14369b589fca59e68dbbb4a534f3a83d2511d6b5fb687b9f2c558560cd9",
    ".crm/seed.sql": "ba0a41ec7f43edf1d34c50faf001d440ffac024f4f5495cb31362a4dfb29a263",
    ".harness/reference_setup.py": "d830d63cb9f9c81aa2ef2ab88493f1d3657daeab3c711b70f6bb9a6a8a95603b",
    ".gitignore": "67da009f0cb8af2904ddd24d023cd306db7365f5484dc8e79119c222f8c53a1b",
}
EXPECTED_DATABASE_DIGEST = "8a489c5c536717e86295e8e78406b7807b851f5ef7bb3e07f0f6341c09d48d02"
REFERENCE_MARKER_DIGEST = "24b2005b2f52c1926aedf9d52dd91a8ca204968aeff7bfd6481da04fb0a49bb1"
EXPECTED_DATE = "2026-11-23"
EXPECTED_AVAILABILITY = {
    ("Bluebird Literacy Project", "Northeast Region", EXPECTED_DATE): False,
    ("Mosaic Bicycle Works", "South Region", EXPECTED_DATE): True,
}
ALLOWED_OPERATIONS = {"help", "profile", "availability", "create"}
ALLOWED_ROOT_ENTRIES = {
    ".crm",
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    "crmctl",
}
ALLOWED_CRM_ENTRIES = {
    "runtime",
    "runtime/crm.sqlite3",
    "runtime/crm.sqlite3-shm",
    "runtime/crm.sqlite3-wal",
    "runtime/initialize.lock",
    "seed.sql",
}
ALLOWED_HARNESS_ENTRIES = {
    "reference_setup.py",
    "runtime",
    "runtime/crm-audit.jsonl",
    "verify.py",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected CRM input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file() or file_sha256(marker) != REFERENCE_MARKER_DIGEST
    ):
        fail("reference marker is not the protected reference-patch marker")

    for directory, allowed in (
        (ROOT / ".crm", ALLOWED_CRM_ENTRIES),
        (ROOT / ".harness", ALLOWED_HARNESS_ENTRIES),
    ):
        nested_unexpected = sorted(
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.relative_to(directory).as_posix() not in allowed
        )
        if nested_unexpected:
            fail(
                f"unexpected scratch artifact under {directory.name}: "
                + ", ".join(nested_unexpected)
            )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite CRM registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            profile = connection.execute(
                """
                SELECT profile_key, default_date, preferred_quantity
                FROM operational_profile ORDER BY profile_key
                """
            ).fetchall()
            availability = connection.execute(
                """
                SELECT name, location, service_date, available
                FROM availability ORDER BY name, location, service_date
                """
            ).fetchall()
            records = connection.execute(
                """
                SELECT stable_id, name, location, service_date, quantity,
                       status, lifecycle
                FROM crm_records ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            metadata = connection.execute(
                """
                SELECT metadata_key, metadata_value
                FROM registry_metadata ORDER BY metadata_key
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        fail(f"CRM registry database is unreadable: {error}")

    if integrity != ("ok",):
        fail("CRM registry database integrity check failed")
    snapshot = {
        "profile": [list(row) for row in profile],
        "availability": [list(row) for row in availability],
        "records": [list(row) for row in records],
        "notifications": [list(row) for row in notifications],
        "metadata": [list(row) for row in metadata],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "CRM state is not the exact permitted result; the selected record, "
            "priority, quantity, date, existing records, allocator, or notifications differ"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine crmctl execution trace was produced")
    events: list[dict] = []
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
            fail(f"audit event {number} was not emitted intact by crmctl")
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
    reference_solution_present = (ROOT / ".reference_solution").is_file()
    if any(
        event.get("reference_solution_present") is not reference_solution_present
        for event in events
    ):
        fail("execution evidence belongs to a different reference-patch state")
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an extra read, write, notification, or unknown operation was executed")
    if len(events) != 5:
        fail(
            "expected exactly one root help, one profile, two availability, "
            "and one create operation"
        )
    if any(not event.get("success") for event in events):
        fail("every required CRM operation must succeed")

    help_event, profile, first_check, second_check, created = events
    if help_event.get("operation") != "help":
        fail("the executable's built-in root help was not opened first")
    if profile.get("operation") != "profile":
        fail("the saved operational profile was not retrieved first")
    if (
        profile.get("default_date") != EXPECTED_DATE
        or profile.get("preferred_quantity") != 1
    ):
        fail("the profile operation did not return the saved profile intact")

    checks = [first_check, second_check]
    if [event.get("operation") for event in checks] != [
        "availability",
        "availability",
    ]:
        fail("the second dependency layer was not exactly two availability checks")
    observed = {
        (event.get("name"), event.get("location"), event.get("date")): event.get(
            "available"
        )
        for event in checks
    }
    if observed != EXPECTED_AVAILABILITY:
        fail("availability checks did not use the profile date and both exact scopes")
    if min(event["start_ns"] for event in checks) <= profile["end_ns"]:
        fail("an availability check started before the profile result was available")
    if (
        not overlaps(checks[0], checks[1])
        or not same_harness_parent(checks[0], checks[1])
        or not separate_tool_calls(checks[0], checks[1])
    ):
        fail("availability checks were not concurrent sibling Pi Bash calls")

    if created.get("operation") != "create":
        fail("the final CRM operation was not the single conditional create")
    if created["start_ns"] <= max(event["end_ns"] for event in checks):
        fail("the create began before both availability results were available")
    if (
        created.get("created_id") != "crm-c149"
        or created.get("name") != "Mosaic Bicycle Works"
        or created.get("location") != "South Region"
        or created.get("date") != EXPECTED_DATE
        or created.get("quantity") != 1
        or created.get("status") != "prospect"
    ):
        fail("the create was not exactly for the first available listed option")
    if created["process_group_id"] in {
        profile["process_group_id"],
        first_check["process_group_id"],
        second_check["process_group_id"],
    }:
        fail("the create was not issued as its own later Bash call")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: profile-grounded date, sibling availability checks, priority "
        "selection, and the single conditional CRM create satisfy every constraint"
    )


if __name__ == "__main__":
    main()
