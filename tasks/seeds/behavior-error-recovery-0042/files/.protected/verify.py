#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".travel-runtime"
DATABASE = RUNTIME / "travel.sqlite3"
AUDIT = RUNTIME / "availability-audit.jsonl"
REPORT = ROOT / "availability-report.txt"
SEED = ROOT / ".protected" / "visits_seed.sql"
AUDIT_KEY = b"moonshiner-pi-error-recovery-0042-v1"
EXPECTED_HASHES = {
    ".gitignore": "2db4a96b7dec1ec0ff6d509f88cd7d20b790f2dc91a288b2ec704a9a1d804c1b",
    "README.md": "c657713c92f7a2f8197492176145fb851d985d307b454195a40c1141ad950cd3",
    "visitctl": "7672132c4f190386d669fcf7e933ef519e7b236a445322a6734eb4201a846825",
    ".protected/visits_seed.sql": "af755201b40d026bb0479680d7dd9bd6d8042fdea41d72cfc7d0d925760d0584",
    ".protected/setup.py": "ffe8bf87ec1616b43b1942a980ea5476ddf706caf29f4f3621d85c476e12ca62",
}
TARGETS = {
    ("Kyoto visit", "Kyoto", "2026-09-15"),
    ("Montreal visit", "Montreal", "2026-09-15"),
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_report_exists() -> None:
    if not REPORT.is_file():
        fail("availability-report.txt is missing")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected travel input changed: {relative}")


def load_seed() -> tuple[dict[tuple[str, str, str], str], dict[tuple[str, str, str], int]]:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        visits = {
            (str(row["name"]), str(row["city"]), str(row["visit_date"])): str(
                row["availability"]
            )
            for row in database.execute(
                "SELECT name, city, visit_date, availability FROM visits"
            )
        }
        failures = {
            (str(row["name"]), str(row["city"]), str(row["visit_date"])): int(
                row["remaining"]
            )
            for row in database.execute(
                "SELECT name, city, visit_date, remaining FROM transient_failures"
            )
        }
        return visits, failures
    finally:
        database.close()


def verify_runtime_data(
    expected_visits: dict[tuple[str, str, str], str],
    initial_failures: dict[tuple[str, str, str], int],
    failed_target: tuple[str, str, str],
) -> None:
    if not DATABASE.is_file():
        fail("travel availability runtime is missing")
    database = sqlite3.connect(DATABASE)
    database.row_factory = sqlite3.Row
    try:
        observed_visits = {
            (str(row["name"]), str(row["city"]), str(row["visit_date"])): str(
                row["availability"]
            )
            for row in database.execute(
                "SELECT name, city, visit_date, availability FROM visits"
            )
        }
        if observed_visits != expected_visits:
            fail("the read-only travel availability records changed")
        observed_failures = {
            (str(row["name"]), str(row["city"]), str(row["visit_date"])): int(
                row["remaining"]
            )
            for row in database.execute(
                "SELECT name, city, visit_date, remaining FROM transient_failures"
            )
        }
        expected_failures = dict(initial_failures)
        expected_failures[failed_target] -= 1
        if observed_failures != expected_failures:
            fail("the transient failure was not consumed exactly once")
        state_rows = database.execute(
            "SELECT event_id, arguments_json, start_ns, end_ns, outcome "
            "FROM operation_state ORDER BY sequence"
        ).fetchall()
        if len(state_rows) != 3 or any(row["end_ns"] is None for row in state_rows):
            fail("the executable did not complete exactly three recorded operations")
    except sqlite3.Error as error:
        fail(f"travel runtime is invalid: {error}")
    finally:
        database.close()


def load_events() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no genuine visitctl execution trace was produced")
    events: list[dict[str, Any]] = []
    for number, raw in enumerate(AUDIT.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(
            AUDIT_KEY,
            canonical_json(event).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by visitctl")
        events.append(event)
    return sorted(events, key=lambda item: int(item.get("start_ns", -1)))


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(int(first["start_ns"]), int(second["start_ns"])) < min(
        int(first["end_ns"]), int(second["end_ns"])
    )


def sibling_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
        and first.get("process_pid") != second.get("process_pid")
        and first.get("process_group_id") != second.get("process_group_id")
        and first.get("process_pid") == first.get("process_group_id")
        and second.get("process_pid") == second.get("process_group_id")
        and first.get("session_id") == first.get("process_pid")
        and second.get("session_id") == second.get("process_pid")
    )


def request_tuple(event: dict[str, Any]) -> tuple[str, str, str]:
    return (str(event.get("name")), str(event.get("city")), str(event.get("date")))


def expected_result(
    target: tuple[str, str, str], availability: str
) -> dict[str, str]:
    name, city, visit_date = target
    return {
        "availability": availability,
        "city": city,
        "date": visit_date,
        "name": name,
    }


def verify_execution(
    events: list[dict[str, Any]],
    visits: dict[tuple[str, str, str], str],
    initial_failures: dict[tuple[str, str, str], int],
) -> tuple[str, str, str]:
    if len(events) != 3:
        fail("expected exactly two initial lookups and one retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("an operation other than exact availability lookup was recorded")

    initial = events[:2]
    retry = events[2]
    if {request_tuple(event) for event in initial} != TARGETS:
        fail("the initial pair was not the two required exact lookups")
    if not overlaps(initial[0], initial[1]) or not sibling_tool_calls(
        initial[0], initial[1]
    ):
        fail("the initial lookups were not concurrent sibling Pi Bash calls")

    failures = [
        event for event in initial if event.get("outcome") == "temporary_unavailable"
    ]
    successes = [event for event in initial if event.get("outcome") == "success"]
    if len(failures) != 1 or len(successes) != 1:
        fail("the initial pair did not preserve one success alongside one transient failure")
    failed_target = request_tuple(failures[0])
    if initial_failures.get(failed_target, 0) < 1:
        fail("the recorded transient error does not match the protected failure plan")
    if request_tuple(retry) != failed_target:
        fail("the retry did not repeat only the exact failed lookup")
    if retry.get("outcome") != "success" or retry.get("success") is not True:
        fail("the exact failed lookup did not succeed on retry")
    if int(retry["start_ns"]) <= max(int(event["end_ns"]) for event in initial):
        fail("the retry began before both initial lookups finished")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not made as a later Pi Bash call")

    for event in successes + [retry]:
        target = request_tuple(event)
        availability = visits.get(target)
        if availability is None:
            fail("a successful response does not correspond to protected travel data")
        if event.get("result_digest") != digest(expected_result(target, availability)):
            fail("a reported success was not the genuine visitctl response")
    if failures[0].get("result_digest") is not None:
        fail("the transient failure improperly carried a successful result")
    return failed_target


def verify_report(visits: dict[tuple[str, str, str], str]) -> None:
    lines = [f"{name}: {visits[(name, city, visit_date)]}" for name, city, visit_date in sorted(TARGETS)]
    expected = "\n".join(lines) + "\n"
    if REPORT.read_text(encoding="utf-8") != expected:
        fail("availability-report.txt is not exactly the requested two-line report")


def main() -> None:
    verify_report_exists()
    verify_protected_inputs()
    visits, initial_failures = load_seed()
    events = load_events()
    failed_target = verify_execution(events, visits, initial_failures)
    verify_runtime_data(visits, initial_failures, failed_target)
    verify_report(visits)
    print("PASS: exact report and genuine Pi partial-failure recovery are verified")


if __name__ == "__main__":
    main()
