#!/usr/bin/env python3
"""Deterministic protected verification for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".availability-runtime" / "execution.jsonl"
REPORT_PATH = ROOT / "availability-report.txt"
KEY_PATH = ROOT / ".protected" / "audit.key"

EXPECTED_HASHES = {
    "availability-check": "e0436f228389ccc700116e6a86f5ad6d2098cb6bb8a8ca326b05fac7dad1d733",
    ".availability/records.json": "fcd987c0a56009e265fc41b0eeeae2ecad632bf9669e292f36cde602f6d71da1",
    "AVAILABILITY.md": "169377125605599469f741b0166ff25cf3d7a406dfcac4e1a2f5d6eb43c2c8d4",
    ".gitignore": "d35e010260a64bea23bd719026ee6f85c900d16e1d0c866a21c97365c9400df4",
    ".protected/audit.key": "4f68ba0bf281c33ca7e0b1d9cdbb732f37ef2f102c48d7c87b23153cf928273d",
    ".protected/reference_setup.py": "edc0b9f95abed156f94bb7e9658ebedb0ce0f3de47d15a40ed24f33d2a4af799",
}
EXPECTED_QUERIES = {
    ("Policy review", "Beacon", "2026-09-23"),
    ("Newsletter draft", "Clover", "2026-09-23"),
}
EXPECTED_REPORT = (
    "- Policy review at Beacon: available\n"
    "- Newsletter draft at Clover: unavailable\n"
)
ALLOWED_ROOT_ENTRIES = {
    ".availability",
    ".availability-runtime",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    "AVAILABILITY.md",
    "availability-check",
    "availability-report.txt",
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
        if not path.is_file() or path.is_symlink() or file_sha256(path) != expected:
            fail(f"protected availability input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected sandbox-root artifact: " + ", ".join(unexpected))
    if not (ROOT / ".availability-runtime").is_dir():
        fail("availability runtime directory is missing")
    runtime_entries = sorted(
        path.name for path in (ROOT / ".availability-runtime").iterdir()
    )
    if runtime_entries != ["execution.jsonl"]:
        fail("unexpected availability runtime artifact")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file() or AUDIT_PATH.is_symlink():
        fail("no genuine availability executable trace was produced")
    events = []
    key = KEY_PATH.read_bytes()
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"execution event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"execution event {number} was not emitted intact by the executable")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    try:
        return max(first["start_ns"], second["start_ns"]) < min(
            first["end_ns"], second["end_ns"]
        )
    except (KeyError, TypeError):
        return False


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
    return event.get("item"), event.get("site"), event.get("date")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly two initial checks and one retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than the read-only availability check was executed")

    initial = events[:2]
    retry = events[2]
    if {query(event) for event in initial} != EXPECTED_QUERIES:
        fail("the first action did not contain exactly the two requested checks")
    if not overlaps(initial[0], initial[1]):
        fail("the initial checks did not execute concurrently")
    if not same_harness_parent(initial[0], initial[1]):
        fail("the initial checks were not sibling Pi Bash tool calls")
    if not separate_tool_calls(initial[0], initial[1]):
        fail("the initial checks were combined instead of using separate Bash calls")

    successes = [event for event in initial if event.get("success") is True]
    failures = [event for event in initial if event.get("success") is False]
    if len(successes) != 1 or len(failures) != 1:
        fail("the required partial-failure outcome was not preserved")
    successful, failed = successes[0], failures[0]
    if query(successful) != ("Policy review", "Beacon", "2026-09-23"):
        fail("the successful Policy review branch was not retained")
    if successful.get("result_availability") != "available":
        fail("the Policy review result was not grounded in the registry response")
    if query(failed) != ("Newsletter draft", "Clover", "2026-09-23"):
        fail("the transient failure was not the Newsletter draft branch")
    if (
        failed.get("retryable") is not True
        or failed.get("error_code") != "temporary_availability_failure"
    ):
        fail("the failed branch was not the executable's retryable transient failure")

    try:
        initial_end = max(event["end_ns"] for event in initial)
    except (KeyError, TypeError):
        fail("the initial execution timing evidence is malformed")
    if not isinstance(retry.get("start_ns"), int) or retry["start_ns"] <= initial_end:
        fail("the retry began before both initial results were available")
    if query(retry) != query(failed):
        fail("the retry targeted a branch other than the failed check")
    if retry.get("argv") != failed.get("argv"):
        fail("the failed check was not retried with the exact same invocation")
    if retry.get("success") is not True or retry.get("retryable") is not False:
        fail("the exact retry did not produce a successful response")
    if retry.get("result_availability") != "unavailable":
        fail("the Newsletter draft report value was not grounded in its successful retry")
    if retry.get("attempt") != 2 or failed.get("attempt") != 1:
        fail("the failed check was not retried exactly once")
    if successful.get("attempt") != 1:
        fail("the already-successful check was repeated")
    initial_groups = {event.get("process_group_id") for event in initial}
    if retry.get("process_group_id") in initial_groups:
        fail("the retry was not issued in a new Pi Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file() or REPORT_PATH.is_symlink():
        fail("availability-report.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability-report.txt is not the exact grounded two-bullet report")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_execution(load_events())
    verify_report()
    print("PASS: exact report and genuine Pi partial-failure recovery verified")


if __name__ == "__main__":
    main()
