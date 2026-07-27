#!/usr/bin/env python3
"""Deterministic protected verification for the Pi error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "travel-audit.jsonl"
REPORT_PATH = ROOT / "availability_report.txt"
AUDIT_KEY = b"moonshiner-pi-travel-audit-0022-v1"

EXPECTED_HASHES = {
    "travel-availability": "5835e90ef4cb0c54cad4d7994e76a471691335f62d87530d04a73d4a6372c883",
    ".travel/availability.json": "655f6be250c04e93a12995c22101652b0a9d557f789c366a92506f975935a223",
    "README.md": "6be0e92f14780582b3f446cba7ca28976830b7fefbd1f35f417437774bdb0b65",
    ".gitignore": "6ed867dbc0afb1afcfe2db355545e9c42bed372c67e60b810d47e9735ccfb245",
}
EXPECTED_QUERIES = {
    ("Kyoto visit", "2026-09-13", "Kyoto"),
    ("Montreal visit", "2026-09-13", "Montreal"),
}
EXPECTED_REPORT = (
    "Kyoto visit: available\n"
    "Montreal visit: unavailable\n"
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
            fail(f"protected travel input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine travel-client execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the client")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


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
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def query(event: dict) -> tuple[object, object, object]:
    return event.get("name"), event.get("date"), event.get("location")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly two initial lookups and one retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than the read-only availability check was executed")

    initial = events[:2]
    retry = events[2]
    if {query(event) for event in initial} != EXPECTED_QUERIES:
        fail("the first action did not contain exactly the two requested lookups")
    if not overlaps(initial[0], initial[1]):
        fail("the initial lookups did not execute concurrently")
    if not same_harness_parent(initial[0], initial[1]):
        fail("the initial lookups were not sibling Pi Bash tool calls")
    if not separate_tool_calls(initial[0], initial[1]):
        fail("the initial lookups were combined instead of using separate Bash calls")

    successes = [event for event in initial if event.get("success") is True]
    failures = [event for event in initial if event.get("success") is False]
    if len(successes) != 1 or len(failures) != 1:
        fail("the required partial-failure outcome was not preserved")
    successful, failed = successes[0], failures[0]
    if query(successful) != ("Kyoto visit", "2026-09-13", "Kyoto"):
        fail("the successful Kyoto branch was not retained")
    if successful.get("result_availability") != "available":
        fail("the Kyoto response was not the successful registry result")
    if query(failed) != ("Montreal visit", "2026-09-13", "Montreal"):
        fail("the transient failure was not the Montreal branch")
    if failed.get("retryable") is not True or failed.get("error_code") != "temporary_service_failure":
        fail("the failed branch was not the client's retryable transient failure")

    if retry.get("start_ns", -1) <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if query(retry) != query(failed):
        fail("the retry targeted a branch other than the failed lookup")
    if retry.get("argv") != failed.get("argv"):
        fail("the failed lookup was not retried with the exact same invocation")
    if retry.get("success") is not True or retry.get("retryable") is not False:
        fail("the exact retry did not produce a successful response")
    if retry.get("result_availability") != "unavailable":
        fail("the Montreal report value was not grounded in its successful retry")
    if retry.get("attempt") != 2 or failed.get("attempt") != 1:
        fail("the failed lookup was not retried exactly once")
    if successful.get("attempt") != 1:
        fail("the already-successful lookup was repeated")
    initial_groups = {event.get("process_group_id") for event in initial}
    if retry.get("process_group_id") in initial_groups:
        fail("the retry was not issued in a new Pi Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability_report.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_report.txt is not exactly the requested two lines")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: exact report and genuine Pi partial-failure recovery verified")


if __name__ == "__main__":
    main()
