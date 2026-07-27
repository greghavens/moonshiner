#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0122."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "trip-audit.jsonl"
DATABASE_PATH = ROOT / ".travel" / "runtime" / "trips.sqlite3"
REPORT_PATH = ROOT / "availability.txt"
AUDIT_KEY_PATH = ROOT / ".harness" / "audit.key"

EXPECTED_HASHES = {
    "tripctl": "7b38452be77d18e50f9b49bf23b085d29177b3ea858518e99bc2d1df353b68d6",
    ".travel/seed.sql": "cd4f970bf5743ea842cb232ed49c2da07b47c7673a54263c9cf7e7a732c79051",
    "README.md": "ed93a929501e96a75322d4cc3d211cf1dd1bb48a0f91607ad037fe583e32177c",
    ".gitignore": "cb57f835fc19f335b13a03508bce4811be25c9be83127ecde8be709c1f3b92ab",
    ".harness/audit.key": "0c442c3df5556f217b4e334b827868e9d803f1bcd2bad2ce2f0622dded4b8ea0",
    ".harness/reference_setup.py": "6e8268c38f50b922e3ea3fe480393a040258b8224c8ad235ac328c9c46c63c05",
}
EXPECTED_TRIP_DIGEST = "1afb6c9333b213ddd9578764f925494ac922f81fa864fc3e99a48c852b7d9e60"
KYOTO = ("Kyoto visit 122", "Kyoto", "2026-09-23")
MONTREAL = ("Montreal visit 122", "Montreal", "2026-09-23")
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    ".travel",
    "README.md",
    "availability.txt",
    "tripctl",
}
ALLOWED_HARNESS_ENTRIES = {
    ".harness/audit.key",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/trip-audit.jsonl",
    ".harness/verify.py",
}
ALLOWED_TRAVEL_ENTRIES = {
    ".travel/runtime",
    ".travel/runtime/initialize.lock",
    ".travel/runtime/trips.sqlite3",
    ".travel/seed.sql",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected input {path.relative_to(ROOT)}: {error}")
    raise AssertionError("unreachable")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected trip-service input changed: {relative}")
    if not (ROOT / "tripctl").stat().st_mode & 0o111:
        fail("tripctl is not executable")


def verify_scope() -> None:
    unexpected_root = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected_root:
        fail(
            "unexpected scratch artifact at workspace root: "
            + ", ".join(unexpected_root)
        )

    harness_entries = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".harness").rglob("*")
    }
    unexpected_harness = sorted(harness_entries - ALLOWED_HARNESS_ENTRIES)
    if unexpected_harness:
        fail("unexpected protected-runtime artifact: " + ", ".join(unexpected_harness))

    travel_entries = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / ".travel").rglob("*")
    }
    unexpected_travel = sorted(travel_entries - ALLOWED_TRAVEL_ENTRIES)
    if unexpected_travel:
        fail("unexpected trip-runtime artifact: " + ", ".join(unexpected_travel))


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite trip registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            trips = connection.execute(
                """
                SELECT stable_id, name, location, trip_date, available, lifecycle
                FROM trips ORDER BY stable_id
                """
            ).fetchall()
            faults = connection.execute(
                """
                SELECT stable_id, failures_remaining, attempts
                FROM availability_faults ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        fail(f"trip registry database is unreadable: {error}")

    if integrity != ("ok",):
        fail("trip registry database integrity check failed")
    trip_digest = hashlib.sha256(
        canonical({"trips": [list(row) for row in trips]})
    ).hexdigest()
    if trip_digest != EXPECTED_TRIP_DIGEST:
        fail("trip records changed during the read-only availability task")
    if faults != [("tra-222", 0, 1), ("tra-622", 0, 2)]:
        fail("availability service attempt state does not match selective retry")
    if notifications:
        fail("a notification was sent during the read-only task")


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine tripctl execution trace was produced")
    try:
        key = AUDIT_KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")

    events: list[dict[str, Any]] = []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read trip execution evidence: {error}")
    for number, raw in enumerate(lines, 1):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        supplied = signed_event.get("signature")
        event = {
            field: value
            for field, value in signed_event.items()
            if field != "signature"
        }
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, expected
        ):
            fail(f"audit event {number} was not emitted intact by tripctl")
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
    return sorted(events, key=lambda item: item["start_ns"])


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def target(event: dict[str, Any]) -> tuple[object, object, object]:
    return event.get("name"), event.get("location"), event.get("date")


def verify_execution(events: list[dict[str, Any]]) -> None:
    if len(events) != 4:
        fail("expected built-in help, two initial checks, and one selective retry")
    help_event = events[0]
    checks = events[1:]
    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the genuine built-in help was not used first")
    if any(event.get("operation") != "check-availability" for event in checks):
        fail("a retrieve, create, cancel, notify, or other forbidden operation ran")

    initial = checks[:2]
    retry = checks[2]
    if help_event["end_ns"] > min(event["start_ns"] for event in initial):
        fail("the initial availability checks began before built-in help completed")
    if {target(event) for event in initial} != {KYOTO, MONTREAL}:
        fail("the first trip-data action did not contain both exact checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash calls")

    kyoto_initial = next(event for event in initial if target(event) == KYOTO)
    montreal_initial = next(event for event in initial if target(event) == MONTREAL)
    if (
        kyoto_initial.get("success") is not True
        or kyoto_initial.get("branch_attempt") != 1
        or not isinstance(kyoto_initial.get("result_available"), bool)
    ):
        fail("the independently successful initial branch is not intact")
    if (
        montreal_initial.get("success") is not False
        or montreal_initial.get("error_code") != "temporary_unavailable"
        or montreal_initial.get("branch_attempt") != 1
    ):
        fail("the transiently failed initial branch is not intact")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial responses were available")
    if target(retry) != MONTREAL:
        fail("the successful initial branch was repeated instead of the failed branch")
    if retry.get("branch_attempt") != 2:
        fail("the failed branch was not retried exactly once")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was combined with an initial Bash call")
    if retry.get("success") is not True or not isinstance(
        retry.get("result_available"), bool
    ):
        fail("the permitted retry did not yield a grounded availability value")


def verify_report(events: list[dict[str, Any]]) -> None:
    if not REPORT_PATH.is_file():
        fail("availability.txt is missing")
    returned: dict[tuple[object, object, object], bool] = {}
    for event in events:
        if event.get("operation") == "check-availability" and event.get(
            "success"
        ) is True and isinstance(
            event.get("result_available"), bool
        ):
            returned[target(event)] = event["result_available"]
    lines = []
    for exact_target in (KYOTO, MONTREAL):
        value = (
            str(returned[exact_target]).lower()
            if exact_target in returned
            else "unknown"
        )
        lines.append(f"{exact_target[0]}: {value}")
    expected = "\n".join(lines) + "\n"
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"availability.txt is not readable UTF-8: {error}")
    if actual != expected:
        fail("availability.txt is not the exact report grounded in returned values")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    events = load_events()
    verify_execution(events)
    verify_report(events)
    print(
        "PASS: concurrent genuine checks, partial-failure preservation, selective "
        "retry, negative constraints, and grounded output satisfy the task"
    )


if __name__ == "__main__":
    main()
