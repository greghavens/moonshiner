#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi expense recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = ROOT / ".expenses" / "records.json"
NOTIFICATIONS_PATH = ROOT / ".expenses" / "notifications.json"
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expense-audit.jsonl"
REPORT_PATH = ROOT / "expense-availability.md"
AUDIT_KEY = b"moonshiner-pi-expense-audit-0134-v1"
TARGETS = (
    ("Train fare 134", "Chicago", "2026-09-17"),
    ("Team lunch 134", "Boston", "2026-09-17"),
)

# Filled with immutable digests after the authored environment is finalized.
EXPECTED_HASHES = {
    ".gitignore": "f91aaed996c7f31381bdd5bddefed8ed5db06ec5f87cb6dbbbaa50f0bb589263",
    "EXPENSES.md": "19d26ab6f449c1f8d07c9f24b5a578b65b3467385b4e7c4d176eb9a7c9089efe",
    "expensectl": "ac749ea5dd1dd13e240dd826ebf6dc097c19d21e485e9f0565e3bd19ca5b8c48",
    ".expenses/records.json": "d8e4fc28959c0ba91da02bf21c1b839ea403b6bc57f5e3532905886aceb49ff2",
    ".expenses/notifications.json": "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570",
}
FORBIDDEN_OPERATIONS = {
    "get",
    "list",
    "create",
    "update",
    "cancel",
    "notify",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected expense input changed: {relative}")


def load_register() -> dict[str, Any]:
    try:
        payload = json.loads(RECORDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"expense register is unreadable: {error}")
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        fail("expense register has an invalid shape")
    return payload


def target_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name, location, _ in TARGETS:
        matches = [
            record
            for record in payload["records"]
            if record.get("name") == name and record.get("location") == location
        ]
        if len(matches) != 1:
            fail("each protected expense target must resolve uniquely")
        records.append(matches[0])
    return records


def expected_response(record: dict[str, Any], date: str) -> dict[str, Any]:
    schedule = record.get("availability")
    details = schedule.get(date) if isinstance(schedule, dict) else None
    if not isinstance(details, dict):
        return {
            "name": record["name"],
            "date": date,
            "location": record["location"],
            "availability": "unknown",
        }
    response = {
        "name": record["name"],
        "date": date,
        "location": record["location"],
    }
    response.update(details)
    return response


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expensectl execution trace was produced")
    events: list[dict[str, Any]] = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(
            AUDIT_KEY, canonical_bytes(event), hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature,
            expected,
        ):
            fail(f"execution event {number} was not emitted intact by expensectl")
        events.append(event)
    return sorted(events, key=lambda event: event.get("started_ns", -1))


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["ended_ns"],
        second["ended_ns"],
    )


def same_harness_parent(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def direct_tool_call(event: dict[str, Any]) -> bool:
    return (
        isinstance(event.get("process_group_id"), int)
        and event.get("process_pid") == event["process_group_id"]
        and event.get("session_id") == event["process_pid"]
    )


def separate_tool_calls(
    first: dict[str, Any],
    second: dict[str, Any],
) -> bool:
    return (
        direct_tool_call(first)
        and direct_tool_call(second)
        and first["process_group_id"] != second["process_group_id"]
    )


def semantic_target(event: dict[str, Any]) -> tuple[Any, Any, Any]:
    return event.get("name"), event.get("location"), event.get("date")


def verify_success(
    event: dict[str, Any],
    response: dict[str, Any],
) -> None:
    if event.get("success") is not True or event.get("transient") is not False:
        fail("a required successful response was not recorded as successful")
    if event.get("result_digest") != digest(response):
        fail("a successful response does not match the protected expense data")
    if event.get("returned_fields") != sorted(response):
        fail("the successful response field evidence is incomplete")


def verify_execution(
    events: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden expense operation was executed")
    if len(events) != 3:
        fail("expected two initial checks and exactly one failed-branch retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("only availability operations are permitted")

    initial = events[:2]
    retry = events[2]
    if {semantic_target(event) for event in initial} != set(TARGETS):
        fail("the initial action did not contain both requested exact checks")
    if not overlaps(initial[0], initial[1]):
        fail("the initial checks did not overlap")
    if not same_harness_parent(initial[0], initial[1]):
        fail("the initial checks were not sibling Pi Bash-tool calls")
    if not separate_tool_calls(initial[0], initial[1]):
        fail("the initial checks were combined or shell-backgrounded")

    if retry.get("started_ns", 0) <= max(
        event.get("ended_ns", 0) for event in initial
    ):
        fail("the retry began before both initial responses were returned")
    if not direct_tool_call(retry) or retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not issued as a later direct Bash-tool call")

    by_target = {target: record for target, record in zip(TARGETS, records)}
    initial_by_target = {semantic_target(event): event for event in initial}
    first_target, failed_target = TARGETS
    first_initial = initial_by_target[first_target]
    failed_initial = initial_by_target[failed_target]

    first_response = expected_response(by_target[first_target], first_target[2])
    failed_response = expected_response(
        by_target[failed_target],
        failed_target[2],
    )
    verify_success(first_initial, first_response)

    if (
        failed_initial.get("success") is not False
        or failed_initial.get("transient") is not True
        or failed_initial.get("error_code") != "temporary_unavailable"
        or failed_initial.get("error") != "temporary_unavailable"
    ):
        fail("the expected initial transient failure was not preserved")
    if semantic_target(retry) != failed_target:
        fail("the retry did not target only the transiently failed expense")
    if retry.get("stable_id") != failed_initial.get("stable_id"):
        fail("the retry switched away from the failed expense branch")
    verify_success(retry, failed_response)

    if sum(semantic_target(event) == first_target for event in events) != 1:
        fail("the successful Train fare branch was repeated")
    if sum(semantic_target(event) == failed_target for event in events) != 2:
        fail("the Team lunch branch was not retried exactly once")


def report_value(response: dict[str, Any], key: str) -> str:
    value = response.get(key)
    return value if isinstance(value, str) and value else "unknown"


def verify_report(records: list[dict[str, Any]]) -> None:
    if not REPORT_PATH.is_file():
        fail("expense-availability.md is missing")
    responses = [
        expected_response(record, target[2])
        for record, target in zip(records, TARGETS)
    ]
    rows = []
    for target, response in zip(TARGETS, responses):
        rows.append(
            "| "
            + " | ".join(
                (
                    target[0],
                    report_value(response, "date"),
                    report_value(response, "location"),
                    report_value(response, "availability"),
                    report_value(response, "reason"),
                    report_value(response, "processing_window"),
                )
            )
            + " |\n"
        )
    expected = (
        "| Expense | Date | Location | Availability | Reason | Processing window |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        + "".join(rows)
    )
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("expense-availability.md is not the exact grounded two-row table")


def verify_notifications_unchanged() -> None:
    try:
        notifications = json.loads(
            NOTIFICATIONS_PATH.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        fail(f"notification register is unreadable: {error}")
    if notifications != []:
        fail("expense notifications changed")


def main() -> None:
    verify_protected_inputs()
    payload = load_register()
    records = target_records(payload)
    verify_execution(load_events(), records)
    verify_report(records)
    verify_notifications_unchanged()
    print("PASS: genuine Pi parallel checks and failed-branch-only retry verified")


if __name__ == "__main__":
    main()
