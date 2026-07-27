#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "project-audit.jsonl"
REPORT_PATH = ROOT / "availability_report.txt"
AUDIT_KEY = b"moonshiner-pi-project-audit-error-recovery-0090-v1"

EXPECTED_HASHES = {
    "project-registry": "91470073559981323be27ee6ac85ac99096461f07a953c06e4b8fe2e1ab4970f",
    ".projects/records.json": "d02e39ad75a26a512dee5d52374f60c2168978e0e435f7fd1455c2688be94594",
    "README.md": "bf36477496a1eb9a55750a24aa95d82167fcd2c6a53674ab7e75812ef452428b",
    ".gitignore": "6ed867dbc0afb1afcfe2db355545e9c42bed372c67e60b810d47e9735ccfb245",
}
WATER = ("Water sampling protocol", "River Program", "2026-09-24")
DONOR = ("Donor database cleanup", "Museum Program", "2026-09-24")
EXPECTED_REPORT = (
    "Water sampling protocol | River Program | 2026-09-24 | available\n"
    "Donor database cleanup | Museum Program | 2026-09-24 | unavailable\n"
    "Retried branch: Water sampling protocol.\n"
)
FORBIDDEN_OPERATIONS = {
    "get",
    "create",
    "update",
    "cancel",
    "notify",
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
            fail(f"protected project-registry input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine project-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def query(event: dict) -> tuple[str | None, str | None, str | None]:
    return event.get("name"), event.get("location"), event.get("date")


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first["parent_pid"] == second["parent_pid"]
        and first["parent_start_ticks"] == second["parent_start_ticks"]
        and first["parent_start_ticks"] != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden project-registry operation was executed")
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("only availability operations may be executed")

    initial = events[:2]
    retry = events[2]
    if {query(event) for event in initial} != {WATER, DONOR}:
        fail("the first action did not contain the two requested exact checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash-tool calls")

    water_initial = next(event for event in initial if query(event) == WATER)
    donor_initial = next(event for event in initial if query(event) == DONOR)
    if water_initial.get("success") is not False:
        fail("the injected initial failure was not preserved in the audit")
    if water_initial.get("error_kind") != "transient":
        fail("the failed initial branch was not identified as transient")
    if donor_initial.get("success") is not True:
        fail("the independent successful initial branch was not retained")
    if donor_initial.get("availability") != "unavailable":
        fail("the successful initial branch returned the wrong grounded value")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial calls returned")
    if query(retry) != WATER:
        fail("the retry did not target only the transiently failed branch")
    if retry.get("success") is not True or retry.get("availability") != "available":
        fail("the single failed-branch retry did not succeed with its grounded value")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not issued in a later Bash-tool action")
    if (
        retry.get("process_pid") != retry.get("process_group_id")
        or retry.get("session_id") != retry.get("process_pid")
        or retry.get("parent_pid") != initial[0].get("parent_pid")
        or retry.get("parent_start_ticks") != initial[0].get("parent_start_ticks")
    ):
        fail("the retry was not one later Pi Bash-tool call from the same harness")

    counts = {target: sum(query(event) == target for event in events) for target in (WATER, DONOR)}
    if counts != {WATER: 2, DONOR: 1}:
        fail("the successful branch was repeated or the failed branch was not retried once")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability_report.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_report.txt does not contain the grounded ordered result")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: genuine concurrent checks, selective retry, read-only state, and report verified"
    )


if __name__ == "__main__":
    main()
