#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "course-availability-audit.jsonl"
REPORT_PATH = ROOT / "availability.txt"
AUDIT_KEY = b"moonshiner-pi-course-availability-audit-0072-v1"

EXPECTED_HASHES = {
    "course-availability": "dc48a38b455d48dce56067f6b768a6f752cc77ee409588a354c0f321d131e1dc",
    ".catalog/courses.json": "57072f7101057bf549b38def6ccd925b9072de7c13c760e424055a761094032f",
    "COURSES.md": "3837905b0279785b889ccbaa4791ec3ca09bb541b2be488bc7f8c9dbb9d69842",
    ".gitignore": "47915c407617761717bb9002f6db3e1601c54b73355b5d832c5db523f8faf904",
}
MICROECONOMICS = (
    "Microeconomics evening seminar",
    "Downtown Campus",
    "2026-08-17",
)
HISTORY = ("Modern history survey", "North Campus", "2026-08-17")
REQUESTED = {MICROECONOMICS, HISTORY}
REPORT_ORDER = [MICROECONOMICS[0], HISTORY[0]]


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
            fail(f"protected course-catalog input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine course-availability execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the executable")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def branch(event: dict) -> tuple[object, object, object]:
    return (event.get("course"), event.get("campus"), event.get("date"))


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


def successful_value(event: dict) -> str:
    if event.get("operation") != "check" or event.get("success") is not True:
        fail("a required course check did not succeed")
    if event.get("transient") is not False or "error_code" in event:
        fail("a successful course check recorded an error")
    value = event.get("availability")
    if not isinstance(value, str) or not value:
        fail("a successful course check returned no availability string")
    return value


def verify_execution(events: list[dict]) -> dict[str, str]:
    if len(events) != 3:
        fail("expected two initial checks and exactly one failed-branch retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than course availability check was executed")
    if any(branch(event) not in REQUESTED for event in events):
        fail("a check used a course, campus, or date outside the request")

    initial = events[:2]
    retry = events[2]
    if {branch(event) for event in initial} != REQUESTED:
        fail("the first action did not contain exactly the two requested checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash-tool calls")

    succeeded = [event for event in initial if event.get("success") is True]
    failed = [event for event in initial if event.get("success") is False]
    if len(succeeded) != 1 or len(failed) != 1:
        fail("the initial checks did not produce one success and one failure")
    successful = succeeded[0]
    failed_initial = failed[0]
    if (
        failed_initial.get("transient") is not True
        or failed_initial.get("error_code") != "catalog_busy"
        or "availability" in failed_initial
    ):
        fail("the retried branch did not have a genuine transient failure")

    if retry.get("start_ns", -1) <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were returned")
    if branch(retry) != branch(failed_initial):
        fail("the later action did not retry only the failed exact check")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry reused an initial Bash-tool call")
    if not same_harness_parent(retry, successful):
        fail("the retry was not issued by the same active Pi harness")

    successful_branch = branch(successful)
    failed_branch = branch(failed_initial)
    if sum(branch(event) == successful_branch for event in events) != 1:
        fail("the successful initial branch was repeated")
    if sum(branch(event) == failed_branch for event in events) != 2:
        fail("the transiently failed branch was not retried exactly once")
    if successful.get("attempt") != 1 or failed_initial.get("attempt") != 1:
        fail("an initial branch was not its first exact check")
    if retry.get("attempt") != 2:
        fail("the failed branch retry was not its second exact check")

    returned = {
        str(successful["course"]): successful_value(successful),
        str(retry["course"]): successful_value(retry),
    }
    if set(returned) != set(REPORT_ORDER):
        fail("successful outputs do not cover both requested courses")
    return returned


def verify_report(returned: dict[str, str]) -> None:
    if not REPORT_PATH.is_file():
        fail("availability.txt is missing")
    text = REPORT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    if (
        text != "\n".join(lines) + "\n"
        or len(lines) != 2
        or any(not line for line in lines)
    ):
        fail("availability.txt must contain exactly two newline-terminated lines")
    expected_lines = [f"{course}: {returned[course]}" for course in REPORT_ORDER]
    if lines != expected_lines:
        fail("the report is not exactly grounded in successful check outputs")


def main() -> None:
    verify_protected_inputs()
    verify_report(verify_execution(load_events()))
    print(
        "PASS: genuine concurrent checks, selective retry, exact scope, and "
        "grounded course report satisfy all constraints"
    )


if __name__ == "__main__":
    main()
