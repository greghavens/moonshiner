#!/usr/bin/env python3
"""Deterministic protected verification for the Pi partial-failure task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".order-runtime" / "execution.jsonl"
REPORT_PATH = ROOT / "availability-report.md"
AUDIT_KEY = b"moonshiner-pi-order-audit-0026-v1"

EXPECTED_HASHES = {
    "order-availability": "1839a7a991e6f3ee7898f70a7ea4cb2b338cffa57ea362e422c71c12d39afa5c",
    ".orders/availability.json": "547af06a953c4c25264f9558a390905a02d820f2058c5722d5137a38ba64471a",
    "ORDERS.md": "372bef0b68b9dec7c83f60856a4b1809bb09305ac79136a55c6de825261e708a",
    ".gitignore": "f3b0afe37f7e85e82de6818cfe445dc5e768803282a81151c070d7745dcee0e5",
}
EXPECTED_QUERIES = {
    ("Office order", "2026-09-17", "Boise"),
    ("Gift order", "2026-09-17", "Phoenix"),
}
EXPECTED_REPORT = (
    "| Item | Availability |\n"
    "| --- | --- |\n"
    "| Office order | available |\n"
    "| Gift order | unavailable |\n"
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
            fail(f"protected order input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine order-client execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"execution event {number} was not emitted intact by the client")
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
    return event.get("item"), event.get("date"), event.get("location")


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
    if query(successful) != ("Office order", "2026-09-17", "Boise"):
        fail("the successful Office order branch was not retained")
    if successful.get("result_availability") != "available":
        fail("the Office order response was not the successful registry result")
    if query(failed) != ("Gift order", "2026-09-17", "Phoenix"):
        fail("the transient failure was not the Gift order branch")
    if (
        failed.get("retryable") is not True
        or failed.get("error_code") != "temporary_registry_failure"
    ):
        fail("the failed branch was not the client's retryable temporary failure")

    if retry.get("start_ns", -1) <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if query(retry) != query(failed):
        fail("the retry targeted a branch other than the failed lookup")
    if retry.get("argv") != failed.get("argv"):
        fail("the failed lookup was not retried with the exact same invocation")
    if retry.get("success") is not True or retry.get("retryable") is not False:
        fail("the exact retry did not produce a successful response")
    if retry.get("result_availability") != "unavailable":
        fail("the Gift order report value was not grounded in its successful retry")
    if retry.get("attempt") != 2 or failed.get("attempt") != 1:
        fail("the failed lookup was not retried exactly once")
    if successful.get("attempt") != 1:
        fail("the already-successful lookup was repeated")
    initial_groups = {event.get("process_group_id") for event in initial}
    if retry.get("process_group_id") in initial_groups:
        fail("the retry was not issued in a new Pi Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability-report.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability-report.md is not exactly the requested Markdown table")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: exact report and genuine Pi partial-failure recovery verified")


if __name__ == "__main__":
    main()
