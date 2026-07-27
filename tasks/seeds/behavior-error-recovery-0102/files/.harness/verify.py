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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "availability-audit.jsonl"
DATABASE_PATH = ROOT / ".travel" / "runtime" / "trips.sqlite3"
REPORT_PATH = ROOT / "availability.txt"
AUDIT_KEY = b"moonshiner-pi-trip-availability-0102-v1"

EXPECTED_HASHES = {
    "trip-availability": "a74333491da60643cd196a8ee2b6879ee6fafe9af98a02cedd01dee86957d428",
    ".travel/seed.sql": "3345b79efa5545a4d93511ace5e487157bd8432669939960a9498abaa9620732",
    "README.md": "4b47cc9024d0b2faa1456d499c67cf8f101eef39590133db0c6877941dd5ebcf",
    ".gitignore": "cb57f835fc19f335b13a03508bce4811be25c9be83127ecde8be709c1f3b92ab",
    ".harness/reference_setup.py": "eb19fe7691c42196a6bbe860a971ed1681046ce87d5456af0b80f2e85377732b",
}
EXPECTED_TRIP_DIGEST = "1731e928581c1a106adfa4ee761bda1310b1579d9a079555ee640fa2109aae9d"
HALIFAX = ("Halifax conference trip", "Halifax", "2026-10-15")
SANTA_FE = ("Santa Fe field visit", "Santa Fe", "2026-10-15")
ALLOWED_PATHS = {
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/availability-audit.jsonl",
    ".harness/verify.py",
    ".reference_solution",
    ".travel",
    ".travel/runtime",
    ".travel/runtime/initialize.lock",
    ".travel/runtime/trips.sqlite3",
    ".travel/seed.sql",
    "README.md",
    "availability.txt",
    "trip-availability",
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
            fail(f"protected trip availability input changed: {relative}")


def verify_scope() -> None:
    unexpected = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git":
            continue
        rendered = relative.as_posix()
        if rendered not in ALLOWED_PATHS:
            unexpected.append(rendered)
    unexpected.sort()
    if unexpected:
        fail("unexpected scratch artifact: " + ", ".join(unexpected))


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
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"trip registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("trip registry database integrity check failed")
    trip_digest = hashlib.sha256(
        canonical({"trips": [list(row) for row in trips]})
    ).hexdigest()
    if trip_digest != EXPECTED_TRIP_DIGEST:
        fail("trip records changed during the read-only availability audit")
    if faults != [("tra-202", 0, 2), ("tra-602", 0, 1)]:
        fail("availability service attempt state does not match selective retry")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine trip-availability execution trace was produced")
    events = []
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


def target(event: dict) -> tuple[object, object, object]:
    return event.get("name"), event.get("location"), event.get("date")


def verify_execution(events: list[dict]) -> None:
    help_events = [event for event in events if event.get("operation") == "help"]
    checks = [event for event in events if event.get("operation") == "check"]
    if len(help_events) + len(checks) != len(events):
        fail("an operation other than help or an availability check was executed")
    if not checks or not any(
        event["end_ns"] <= checks[0]["start_ns"] for event in help_events
    ):
        fail("the executable's built-in help was not run before trip-data requests")
    if len(checks) != 3:
        fail("expected exactly two initial checks and one selective retry")

    initial = checks[:2]
    retry = checks[2]
    if {target(event) for event in initial} != {HALIFAX, SANTA_FE}:
        fail("the first trip-data action did not contain both exact checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash calls")

    halifax_initial = next(event for event in initial if target(event) == HALIFAX)
    santa_initial = next(event for event in initial if target(event) == SANTA_FE)
    if (
        halifax_initial.get("success") is not False
        or halifax_initial.get("error_code") != "temporary_unavailable"
        or halifax_initial.get("branch_attempt") != 1
    ):
        fail("the transiently failed initial branch is not intact")
    if (
        santa_initial.get("success") is not True
        or santa_initial.get("branch_attempt") != 1
        or not isinstance(santa_initial.get("result_available"), bool)
    ):
        fail("the independently successful initial branch is not intact")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if target(retry) != HALIFAX:
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


def verify_report(events: list[dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("availability.txt is missing")
    returned: dict[tuple[object, object, object], bool] = {}
    for event in events:
        if event.get("success") is True and isinstance(
            event.get("result_available"), bool
        ):
            returned[target(event)] = event["result_available"]
    lines = []
    for exact_target in (HALIFAX, SANTA_FE):
        value = (
            str(returned[exact_target]).lower()
            if exact_target in returned
            else "unknown"
        )
        lines.append(f"{exact_target[0]}: {value}")
    expected = "\n".join(lines) + "\n"
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("availability.txt is not the exact report grounded in returned values")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    events = load_events()
    verify_execution(events)
    verify_report([event for event in events if event.get("operation") == "check"])
    print(
        "PASS: documented genuine checks, concurrency, partial-failure preservation, "
        "selective retry, and grounded availability output satisfy the task"
    )


if __name__ == "__main__":
    main()
