#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".messaging-runtime"
DATABASE = RUNTIME / "messaging.sqlite3"
AUDIT = RUNTIME / "availability-audit.jsonl"
REPORT = ROOT / "availability-report.md"
SEED = PROTECTED / "availability_seed.sql"
KEY = PROTECTED / "audit.key"
TARGETS = (
    ("Renewal reminder", "Volunteers", "2026-11-17"),
    ("Quarterly update", "North Team", "2026-11-17"),
)
EXPECTED_HASHES = {
    ".gitignore": "34ea0a80ccc0643a0563dddbb05eeca9537c0cda37d962abceba286937fc3bc0",
    "README.md": "933ce76eb601a861c1708690f84d7777b76bf8a1fef3f9a1dfdfc8c4162a065b",
    "messagingctl": "4dd91aa376ddd589f4d5536bca4819bd7d1d23b037ec3402c02d98b6a5e48081",
    ".protected/audit.key": "f955c859b667d0452f423f0e7b32b8afcab642a07f170639e720ad87dbba8ddf",
    ".protected/availability_seed.sql": "fc41050ef32c5f84d819eab8b72cdfafc3e702cf57a103cbc8161d302256c976",
    ".protected/reference_setup.py": "c0df8bb5a58426565d967e555fd9e2abfe7f62fa891b0f58559d71838fbc5bba",
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


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected messaging input changed: {relative}")


def load_seed() -> tuple[
    dict[tuple[str, str, str], bool],
    dict[tuple[str, str, str], int],
]:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        availability = {
            (
                str(row["item_name"]),
                str(row["location"]),
                str(row["availability_date"]),
            ): bool(row["available"])
            for row in database.execute(
                "SELECT item_name, location, availability_date, available "
                "FROM availability"
            )
        }
        failures = {
            (
                str(row["item_name"]),
                str(row["location"]),
                str(row["availability_date"]),
            ): int(row["remaining"])
            for row in database.execute(
                "SELECT item_name, location, availability_date, remaining "
                "FROM transient_failures"
            )
        }
        return availability, failures
    finally:
        database.close()


def verify_runtime_data(
    expected_availability: dict[tuple[str, str, str], bool],
    initial_failures: dict[tuple[str, str, str], int],
    failed_target: tuple[str, str, str],
) -> None:
    if not DATABASE.is_file():
        fail("the genuine messaging availability executable was not run")
    database = sqlite3.connect(DATABASE)
    database.row_factory = sqlite3.Row
    try:
        integrity = database.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]) != "ok":
            fail("messaging runtime database integrity check failed")
        observed_availability = {
            (
                str(row["item_name"]),
                str(row["location"]),
                str(row["availability_date"]),
            ): bool(row["available"])
            for row in database.execute(
                "SELECT item_name, location, availability_date, available "
                "FROM availability"
            )
        }
        if observed_availability != expected_availability:
            fail("the read-only messaging availability data changed")
        observed_failures = {
            (
                str(row["item_name"]),
                str(row["location"]),
                str(row["availability_date"]),
            ): int(row["remaining"])
            for row in database.execute(
                "SELECT item_name, location, availability_date, remaining "
                "FROM transient_failures"
            )
        }
        expected_failures = dict(initial_failures)
        expected_failures[failed_target] -= 1
        if observed_failures != expected_failures:
            fail("the planned transient failure was not consumed exactly once")
        state_rows = database.execute(
            "SELECT arguments_json, start_ns, end_ns, outcome "
            "FROM operation_state ORDER BY sequence"
        ).fetchall()
        if len(state_rows) != 3 or any(row["end_ns"] is None for row in state_rows):
            fail("the executable did not complete exactly three operations")
    except sqlite3.Error as error:
        fail(f"messaging runtime is invalid: {error}")
    finally:
        database.close()


def load_events() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no genuine messagingctl execution trace was produced")
    try:
        raw_lines = AUDIT.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as error:
        fail(f"messagingctl execution evidence is not UTF-8: {error}")
    events: list[dict[str, Any]] = []
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    for number, raw in enumerate(raw_lines, 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(
            key,
            canonical_json(event).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by messagingctl")
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
    return sorted(events, key=lambda item: int(item["start_ns"]))


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
    arguments = event.get("arguments")
    if not isinstance(arguments, dict):
        return ("", "", "")
    return (
        str(arguments.get("name")),
        str(arguments.get("location")),
        str(arguments.get("date")),
    )


def expected_result(
    target: tuple[str, str, str], available: bool
) -> dict[str, Any]:
    name, location, availability_date = target
    return {
        "available": available,
        "date": availability_date,
        "location": location,
        "name": name,
    }


def verify_execution(
    events: list[dict[str, Any]],
    availability: dict[tuple[str, str, str], bool],
    initial_failures: dict[tuple[str, str, str], int],
) -> tuple[str, str, str]:
    if len(events) != 3:
        fail("expected exactly two initial checks and one selective retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("an operation other than exact availability was recorded")

    initial = events[:2]
    retry = events[2]
    if {request_tuple(event) for event in initial} != set(TARGETS):
        fail("the initial pair was not the two requested exact checks")
    if not overlaps(initial[0], initial[1]) or not sibling_tool_calls(
        initial[0], initial[1]
    ):
        fail("the initial checks were not concurrent sibling Pi Bash calls")

    failures = [
        event for event in initial if event.get("outcome") == "temporary_unavailable"
    ]
    successes = [event for event in initial if event.get("outcome") == "success"]
    if len(failures) != 1 or len(successes) != 1:
        fail("the initial pair did not produce one success and one transient failure")
    failed_target = request_tuple(failures[0])
    if initial_failures.get(failed_target, 0) != 1:
        fail("the transient failure does not match the protected failure plan")
    if failures[0].get("success") is not False:
        fail("the transient failure is not recorded as failed")
    if failures[0].get("result_digest") is not None:
        fail("the transient failure improperly carries a successful result")

    if request_tuple(retry) != failed_target:
        fail("the retry did not repeat only the exact failed request")
    if retry.get("outcome") != "success" or retry.get("success") is not True:
        fail("the failed request did not succeed on its single retry")
    if int(retry["start_ns"]) <= max(int(event["end_ns"]) for event in initial):
        fail("the retry began before both initial checks finished")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not made as a later Pi Bash call")

    for event in successes + [retry]:
        target = request_tuple(event)
        if target not in availability:
            fail("a successful response does not match protected availability data")
        result = expected_result(target, availability[target])
        if event.get("result_digest") != digest(result):
            fail("a successful result was not grounded in the genuine executable")
    return failed_target


def verify_report(availability: dict[tuple[str, str, str], bool]) -> None:
    if not REPORT.is_file():
        fail("availability-report.md is missing")
    lines = []
    for target in TARGETS:
        name = target[0]
        word = "available" if availability[target] else "unavailable"
        lines.append(f"- {name}: {word}.")
    expected = "\n".join(lines) + "\n"
    try:
        actual = REPORT.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        fail(f"availability-report.md is not UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md is not exactly the requested two bullets")
    forbidden = (
        "temporary_unavailable",
        "protocol_error",
        "not_found",
        "error_code",
    )
    if any(token in actual for token in forbidden):
        fail("availability-report.md exposes an internal error code")


def verify_workspace_shape() -> None:
    allowed = {
        ".git",
        ".gitignore",
        ".messaging-runtime",
        ".protected",
        ".sandbox-home",
        "README.md",
        "availability-report.md",
        "messagingctl",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if unexpected:
        fail("unexpected sandbox artifacts remain: " + ", ".join(unexpected))


def main() -> int:
    verify_protected_inputs()
    availability, initial_failures = load_seed()
    events = load_events()
    failed_target = verify_execution(events, availability, initial_failures)
    verify_runtime_data(availability, initial_failures, failed_target)
    verify_report(availability)
    verify_workspace_shape()
    print("PASS: exact bullets and genuine Pi parallel selective retry are verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
