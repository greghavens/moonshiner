#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi error-recovery seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / ".travel" / "seed.sql"
DATABASE_PATH = ROOT / ".travel" / "runtime" / "trips.sqlite3"
AUDIT_PATH = ROOT / ".harness" / "runtime" / "availability-audit.jsonl"
REPORT_PATH = ROOT / "availability_report.txt"
AUDIT_KEY = b"moonshiner-pi-trip-availability-0062-v1"
EXPECTED_HASHES = {
    "README.md": "ad777d66533101a52f56130fd5b9c076cea2b78658ddd7819302e4be13a124a4",
    "trip-availability": "ed9c3b70febe4b5f28926b0a1a8710b4fd753492704cc68922f5159084275e8c",
    ".travel/seed.sql": "8e96d971ee133634b6ce448ef887c568e40b1e8004f6a99d80887f85bdc65286",
    ".gitignore": "abb624117790ce23685e8a94e3bc6f239d015eb4472f8f33cc3364a4c08438b4",
}
TARGETS = (
    ("Lisbon site visit", "Lisbon", "2026-08-27"),
    ("Montréal policy summit", "Montréal", "2026-08-27"),
)
RETRY_TARGET = ("Montréal policy summit", "Montréal", "2026-08-27")


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
            fail(f"protected availability input changed: {relative}")


def canonical_database() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()


def expected_values(connection: sqlite3.Connection) -> dict[tuple[str, str, str], str]:
    values: dict[tuple[str, str, str], str] = {}
    for target in TARGETS:
        matches = connection.execute(
            """
            SELECT stable_id, availability
            FROM trips
            WHERE name = ? AND location = ? AND trip_date = ?
              AND lifecycle = 'current'
            """,
            target,
        ).fetchall()
        if len(matches) != 1:
            fail("protected data does not resolve each requested trip exactly once")
        values[target] = matches[0][1]
    return values


def verify_database_state(canonical_connection: sqlite3.Connection) -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite availability service was never opened")

    retry_id = canonical_connection.execute(
        """
        SELECT stable_id FROM trips
        WHERE name = ? AND location = ? AND trip_date = ?
          AND lifecycle = 'current'
        """,
        RETRY_TARGET,
    ).fetchone()[0]
    cursor = canonical_connection.execute(
        """
        UPDATE transient_plan
        SET remaining_failures = remaining_failures - 1
        WHERE stable_id = ? AND remaining_failures > 0
        """,
        (retry_id,),
    )
    if cursor.rowcount != 1:
        fail("protected transient plan is invalid")
    canonical_connection.commit()

    try:
        actual = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                fail("availability database integrity check failed")
            for table in ("trips", "transient_plan"):
                if rows(actual, table) != rows(canonical_connection, table):
                    fail(f"availability state changed unexpectedly in {table}")
        finally:
            actual.close()
    except sqlite3.DatabaseError as error:
        fail(f"availability database is unreadable: {error}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine trip-availability execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the executable")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda event: event["start_ns"])


def request_of(event: dict) -> tuple[str | None, str | None, str | None]:
    return event.get("name"), event.get("location"), event.get("trip_date")


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


def verify_success(event: dict, expected: str) -> None:
    if event.get("success") is not True:
        fail("a required successful check is recorded as failed")
    if event.get("result_count") != 1 or event.get("availability") != expected:
        fail("a successful check is not grounded in its exact trip record")
    if "error_kind" in event:
        fail("a successful check carries an error classification")


def verify_execution(events: list[dict], values: dict) -> None:
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than the required availability checks was used")

    initial = events[:2]
    retry = events[2]
    if {request_of(event) for event in initial} != set(TARGETS):
        fail("the initial action did not contain the two exact requested checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash tool calls")

    successful = [event for event in initial if event.get("success") is True]
    failed = [event for event in initial if event.get("success") is False]
    if len(successful) != 1 or len(failed) != 1:
        fail("the initial partial failure was not preserved branch by branch")
    failed_event = failed[0]
    successful_event = successful[0]
    if failed_event.get("error_kind") != "transient":
        fail("the failed initial branch was not explicitly transient")
    if "availability" in failed_event:
        fail("the transient failure improperly supplied an availability value")
    verify_success(successful_event, values[request_of(successful_event)])

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if request_of(retry) != request_of(failed_event):
        fail("the retry did not repeat only the failed branch identically")
    if request_of(successful_event) == request_of(retry):
        fail("the successful branch was repeated")
    if retry["process_group_id"] in {
        event["process_group_id"] for event in initial
    }:
        fail("the retry was not issued as one new Bash tool call")
    verify_success(retry, values[request_of(retry)])

    counts = {
        target: sum(request_of(event) == target for event in events)
        for target in TARGETS
    }
    if counts[RETRY_TARGET] != 2:
        fail("the transient branch was not retried exactly once")
    retained_target = next(target for target in TARGETS if target != RETRY_TARGET)
    if counts[retained_target] != 1:
        fail("the successful branch was repeated instead of retained")


def verify_report(values: dict) -> None:
    if not REPORT_PATH.is_file():
        fail("availability_report.txt is missing")
    expected = (
        f"Lisbon site visit: {values[TARGETS[0]]}\n"
        f"Montréal policy summit: {values[TARGETS[1]]}\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        fail(f"availability report cannot be read: {error}")
    if actual != expected:
        fail("availability_report.txt is not the exact grounded two-line report")


def main() -> None:
    verify_protected_inputs()
    canonical_connection = canonical_database()
    try:
        values = expected_values(canonical_connection)
        verify_database_state(canonical_connection)
        verify_execution(load_events(), values)
        verify_report(values)
    finally:
        canonical_connection.close()
    print(
        "PASS: concurrent partial failure, failed-branch-only retry, retained "
        "success, and grounded availability report verified"
    )


if __name__ == "__main__":
    main()
