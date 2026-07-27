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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expense-availability-audit.jsonl"
DATABASE_PATH = ROOT / ".expenses" / "runtime" / "ledger.sqlite3"
REPORT_PATH = ROOT / "availability_report.txt"
AUDIT_KEY = b"moonshiner-pi-expense-availability-0094-v1"

EXPECTED_HASHES = {
    "expense-ledger": "d490b99a2c0499d18d7c489224b7a5ab91af6e3fe42fb7242509a75844827144",
    ".expenses/seed.sql": "cb236a7021026ab513b64cf3ddfe073f512afb67dd18860b9a9f458e3018c106",
    "README.md": "461fc54989a288054c1814decc4e1d5b4b888220cd1b13735f939857ff223ae1",
    ".gitignore": "f07965a312d320bc87dcfc2c2f2d46f193def546886c4516bd3c860c8f57409f",
}
EXPECTED_DATABASE_DIGEST = "07022546165d291308dcd25c987b58ca4b049ae0f79a7c34a5de4fce54e0663a"
DENVER = ("Denver lodging — policy summit", "Denver", "2026-09-04")
TUCSON = ("Tucson mileage — field sampling", "Tucson", "2026-09-04")
EXPECTED_REPORT = (
    "Denver lodging — policy summit: false\n"
    "Tucson mileage — field sampling: true\n"
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
            fail(f"protected expense-ledger input changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            records = connection.execute(
                """
                SELECT stable_id, name, location, availability_date,
                       available, lifecycle
                FROM expense_availability ORDER BY stable_id
                """
            ).fetchall()
            attempts = connection.execute(
                """
                SELECT name, location, availability_date, attempt_count
                FROM availability_attempts
                ORDER BY name, location, availability_date
                """
            ).fetchall()
            failures = connection.execute(
                """
                SELECT name, location, availability_date, failures_remaining
                FROM transient_failures
                ORDER BY name, location, availability_date
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expense ledger database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("expense ledger database integrity check failed")
    digest = hashlib.sha256(canonical({"records": [list(row) for row in records]})).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("expense availability records changed")
    if attempts != [(*DENVER, 2), (*TUCSON, 1)]:
        fail("availability attempt state does not show one failed-branch retry only")
    if failures != [(*DENVER, 0)]:
        fail("the controlled transient failure was not consumed exactly once")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expense-ledger execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the ledger")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


def query(event: dict) -> tuple[object, object, object]:
    return event.get("name"), event.get("location"), event.get("date")


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
        fail("a non-availability or unknown ledger operation was executed")

    initial = events[:2]
    retry = events[2]
    if {query(event) for event in initial} != {DENVER, TUCSON}:
        fail("the first ledger action did not contain both exact requested checks")
    if any(event.get("attempt_number") != 1 for event in initial):
        fail("an initial availability branch was repeated")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash tool calls")

    initial_by_query = {query(event): event for event in initial}
    denver = initial_by_query[DENVER]
    tucson = initial_by_query[TUCSON]
    if (
        denver.get("success") is not False
        or denver.get("error_code") != "temporary_unavailable"
        or denver.get("transient") is not True
        or "available" in denver
    ):
        fail("the Denver initial branch is not the genuine transient failure")
    if (
        tucson.get("success") is not True
        or tucson.get("available") is not True
        or "error_code" in tucson
    ):
        fail("the Tucson initial success was not preserved")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if query(retry) != DENVER or retry.get("attempt_number") != 2:
        fail("the retry was not exactly the transiently failed branch")
    if retry.get("success") is not True or retry.get("available") is not False:
        fail("the permitted retry did not return the grounded availability value")
    if retry.get("process_group_id") in {
        event["process_group_id"] for event in initial
    }:
        fail("the retry reused an initial Bash tool call")
    if not same_harness_parent(initial[0], retry):
        fail("the retry did not come from the same genuine harness session")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability_report.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_report.txt is not the exact grounded two-line report")


def main() -> None:
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine Pi sibling checks preserved the successful branch, "
        "retried only the transient failure, and produced a grounded report"
    )


if __name__ == "__main__":
    main()
