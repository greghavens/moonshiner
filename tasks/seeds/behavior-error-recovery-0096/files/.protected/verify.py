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
AUDIT_PATH = ROOT / ".protected" / "runtime" / "claim-availability-audit.jsonl"
DATABASE_PATH = ROOT / ".claim-runtime" / "claims.sqlite3"
REPORT_PATH = ROOT / "claim-availability.txt"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"

EXPECTED_HASHES = {
    "claimctl": "e30eebd352895da195d687ae4a6a0b308a59ec4aa710d3a02a6534aa4dc83e7e",
    ".protected/claim_seed.sql": "afca7c9255f9ba5cd195a0c03cd58fad3f4f18fc90ecb20f63fea0c4c3679555",
    ".protected/audit.key": "4a4133753409a5c82c3795543ab6c8ffac62559529a08fe9c553a64ed48b3179",
    "CLAIMS.md": "f37eb2e7b0bb5b37bb2237c47ca0abb8140166fdc48c3b74666e914a2402c824",
    ".gitignore": "ac56e3e03582761d28e6b5f519238bfa21c6eb11d32127dc3fbe278c3121d3e8",
}
EXPECTED_DATABASE_DIGEST = "aaa0297a1d86ed8fcdfff560852e0e6ffe320733f97c2471328008368a6f0fd4"
WATER = (
    "Water damage claim — archive room",
    "Central Office",
    "2026-09-11",
)
LUGGAGE = (
    "Lost baggage claim — conference trip",
    "Travel Desk",
    "2026-09-11",
)
EXPECTED_REPORT = (
    "Water damage claim — archive room: available\n"
    "Lost baggage claim — conference trip: unavailable\n"
)


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
            fail(f"protected claim-registry input changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite claim registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, office, availability_date,
                       available, lifecycle
                FROM claim_availability ORDER BY stable_id
                """
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT name, office, availability_date, attempt_count
                FROM availability_attempts
                ORDER BY name, office, availability_date
                """
            ).fetchall()
            failures = connection.execute(
                """
                SELECT name, office, availability_date, failures_remaining
                FROM transient_failures
                ORDER BY name, office, availability_date
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"claim registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("claim registry database integrity check failed")
    digest = hashlib.sha256(
        canonical({"records": [list(row) for row in records]})
    ).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("claim availability records changed")
    if attempts != [(*LUGGAGE, 1), (*WATER, 2)]:
        fail("attempt state does not show one failed-branch retry only")
    if failures != [(*WATER, 0)]:
        fail("the controlled transient failure was not consumed exactly once")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claimctl execution trace was produced")
    events = []
    audit_key = AUDIT_KEY_PATH.read_bytes()
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
        expected = hmac.new(audit_key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by claimctl")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def query(event: dict) -> tuple[object, object, object]:
    return event.get("name"), event.get("office"), event.get("date")


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
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("a non-availability or unknown claim operation was executed")

    initial = events[:2]
    retry = events[2]
    if {query(event) for event in initial} != {WATER, LUGGAGE}:
        fail("the first claim-data action did not contain both exact requested checks")
    if any(event.get("attempt_number") != 1 for event in initial):
        fail("an initial availability branch was repeated")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash tool calls")

    initial_by_query = {query(event): event for event in initial}
    water = initial_by_query[WATER]
    luggage = initial_by_query[LUGGAGE]
    if (
        water.get("success") is not False
        or water.get("error_code") != "temporary_unavailable"
        or water.get("transient") is not True
        or water.get("retryable") is not True
        or "available" in water
    ):
        fail("the initial water-damage branch is not the genuine transient failure")
    if (
        luggage.get("success") is not True
        or luggage.get("available") is not False
        or "error_code" in luggage
    ):
        fail("the initial baggage success was not preserved")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if query(retry) != WATER or retry.get("attempt_number") != 2:
        fail("the retry was not exactly the transiently failed branch")
    if retry.get("success") is not True or retry.get("available") is not True:
        fail("the permitted retry did not return the grounded availability value")
    if retry.get("process_group_id") in {
        event["process_group_id"] for event in initial
    }:
        fail("the retry reused an initial Bash tool call")
    if not same_harness_parent(initial[0], retry):
        fail("the retry did not come from the same genuine harness session")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("claim-availability.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("claim-availability.txt is not the exact grounded two-line report")


def main() -> None:
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine Pi sibling checks preserved the successful branch, "
        "retried only the transient failure, and produced a scoped grounded report"
    )


if __name__ == "__main__":
    main()
