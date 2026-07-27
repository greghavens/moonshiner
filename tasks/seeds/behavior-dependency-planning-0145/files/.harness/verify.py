#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi support dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "support-audit.jsonl"
DATABASE_PATH = ROOT / ".support" / "runtime" / "support.sqlite3"
AUDIT_KEY = b"moonshiner-pi-support-audit-0145-v1"

EXPECTED_HASHES = {
    "support-registry": "4121ff46432b0d44161faef378c3b6e10398e55216aa03f60645399d409421f4",
    ".support/seed.sql": "cb43563c800d697eafc669c1e1b235a46c9fe471ac86a90e83e12408d2f37a11",
    "README.md": "8f2e9331098d5ac0cf0b4124312e8e910e083da299d8e6615b193b7e63e9b0f7",
    ".gitignore": "dbc7fc2415a946d4a2233f4f0c5825c3778efcc08789976804e111009aafd49a",
}
EXPECTED_DATABASE_DIGEST = "c292cba4fe3a66b4cf1c4664ad7170251806ed40d633de7fda8b5af8bda6a387"
EXPECTED_DATE = "2026-11-19"
EXPECTED_AVAILABILITY = {
    ("Captioning request case", "Cedar Clinic", EXPECTED_DATE): False,
    ("Mobile app login case", "Delta Library", EXPECTED_DATE): True,
}
ALLOWED_OPERATIONS = {"profile", "availability", "create"}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    ".support",
    "README.md",
    "support-registry",
}
ALLOWED_SUPPORT_ENTRIES = {
    "runtime",
    "runtime/initialize.lock",
    "runtime/support.sqlite3",
    "runtime/support.sqlite3-shm",
    "runtime/support.sqlite3-wal",
    "seed.sql",
}
ALLOWED_HARNESS_ENTRIES = {
    "reference_setup.py",
    "runtime",
    "runtime/support-audit.jsonl",
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
            fail(f"protected support-registry input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))

    for directory, allowed in (
        (ROOT / ".support", ALLOWED_SUPPORT_ENTRIES),
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
        fail("the genuine SQLite support registry was never opened")
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
                FROM support_records ORDER BY stable_id
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
    except sqlite3.DatabaseError as exc:
        fail(f"support registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("support registry database integrity check failed")
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
            "support state is not the exact permitted result; the selected record, "
            "quantity, date, other records, allocator, or notifications differ"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine support-registry execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the registry")
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
    if len(events) != 4:
        fail("expected exactly one profile, two availability, and one create operation")
    if any(not event.get("success") for event in events):
        fail("every required support-registry operation must succeed")

    profile, first_check, second_check, created = events
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
        fail("the final support operation was not the single conditional create")
    if created["start_ns"] <= max(event["end_ns"] for event in checks):
        fail("the create began before both availability results were available")
    if (
        created.get("created_id") != "sup-c145"
        or created.get("name") != "Mobile app login case"
        or created.get("location") != "Delta Library"
        or created.get("date") != EXPECTED_DATE
        or created.get("quantity") != 1
        or created.get("status") != "open"
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
        "PASS: profile-grounded date, sibling availability checks, and the single "
        "conditional support-record create satisfy every dependency and scope constraint"
    )


if __name__ == "__main__":
    main()
