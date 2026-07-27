#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

from collections import Counter
import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / ".claims" / "availability.json"
AUDIT_PATH = ROOT / ".harness" / "runtime" / "claim-checks.jsonl"
REPORT_PATH = ROOT / "availability.md"
AUDIT_KEY = b"moonshiner-pi-claim-recovery-0056-v1"
TARGETS = [
    ("Theft claim", "West Office", "2026-11-11"),
    ("Windshield claim", "North Office", "2026-11-11"),
]
EXPECTED_HASHES = {
    ".harness/claim_runtime.py": "819ea89553b9bcb1ede7c6273c7263257bd881d334a2de12a5432eaabdfa159c",
    ".claims/availability.json": "ee29a8d7788bebfe4c24673652325997c7a054533e3f4467bc8fd77341af1b1a",
    "claim-availability": "22b43e1ad2e0d61facf39b3902e233f953790b1d467526b563259909994e31bf",
    "README.md": "6925374e3441a4a99c0c8ff59a1c4cf347efeea0bd534a1f616d2509c78bdf38",
    ".gitignore": "e66f700830af394d29579fbf527e12cf6dce02b01eca4cd7a8a6137922ad832f",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(event: dict) -> tuple[str | None, str | None, str | None]:
    return event.get("name"), event.get("office"), event.get("date")


def verify_protected_inputs() -> None:
    for relative, wanted in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != wanted:
            fail(f"protected sandbox input changed: {relative}")


def load_expected() -> tuple[dict[tuple[str, str, str], bool], tuple[str, str, str]]:
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"protected availability data is unreadable: {error}")

    expected: dict[tuple[str, str, str], bool] = {}
    for target in TARGETS:
        rows = [
            row
            for row in payload.get("claims", [])
            if (row.get("name"), row.get("office"), row.get("date")) == target
        ]
        if len(rows) != 1 or not isinstance(rows[0].get("available"), bool):
            fail("protected availability data does not resolve each target exactly once")
        expected[target] = rows[0]["available"]

    failure_rules = [
        rule
        for rule in payload.get("transient_failures", [])
        if (rule.get("name"), rule.get("office"), rule.get("date")) in set(TARGETS)
        and rule.get("failures") == 1
    ]
    if len(failure_rules) != 1:
        fail("protected failure policy must transiently fail exactly one target once")
    failed_target = (
        failure_rules[0]["name"],
        failure_rules[0]["office"],
        failure_rules[0]["date"],
    )
    return expected, failed_target


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claim-availability execution trace was produced")
    raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    if len(raw_lines) != 3:
        fail("exactly two initial checks and one failed-branch retry are required")

    events: list[dict] = []
    for number, raw in enumerate(raw_lines, 1):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        signature = signed_event.pop("signature", None)
        wanted = hmac.new(AUDIT_KEY, canonical(signed_event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, wanted):
            fail(f"audit event {number} was not emitted intact by the executable")
        if signed_event.get("version") != 1:
            fail("unsupported audit event version")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(signed_event.get(field), int):
                fail(f"audit event {number} has invalid {field}")
        if signed_event["start_ns"] >= signed_event["end_ns"]:
            fail(f"audit event {number} has an invalid interval")
        events.append(signed_event)
    events.sort(key=lambda event: event["start_ns"])
    if len({event.get("event_id") for event in events}) != len(events):
        fail("audit event identifiers are not unique")
    return events


def overlaps(left: dict, right: dict) -> bool:
    return max(left["start_ns"], right["start_ns"]) < min(
        left["end_ns"], right["end_ns"]
    )


def same_harness_parent(left: dict, right: dict) -> bool:
    return (
        left.get("parent_pid") == right.get("parent_pid")
        and left.get("parent_start_ticks") == right.get("parent_start_ticks")
        and left.get("parent_start_ticks") != "unavailable"
    )


def independent_pi_call(event: dict) -> bool:
    return (
        event.get("process_pid") == event.get("process_group_id")
        and event.get("session_id") == event.get("process_pid")
    )


def verify_execution(
    events: list[dict],
    expected: dict[tuple[str, str, str], bool],
    failed_target: tuple[str, str, str],
) -> None:
    if any(event.get("operation") != "check" for event in events):
        fail("a non-check claim operation was executed")

    initial = events[:2]
    retry = events[2]
    if set(map(request, initial)) != set(TARGETS):
        fail("the first availability action did not contain the two requested checks")
    if not overlaps(initial[0], initial[1]):
        fail("the two initial checks did not execute concurrently")
    if not same_harness_parent(initial[0], initial[1]):
        fail("the initial checks were not sibling Pi tool calls")
    if (
        not all(independent_pi_call(event) for event in initial)
        or initial[0]["process_group_id"] == initial[1]["process_group_id"]
    ):
        fail("the initial checks were not two independent Pi bash calls")

    failed_events = [event for event in initial if not event.get("success")]
    successful_events = [event for event in initial if event.get("success")]
    if len(failed_events) != 1 or len(successful_events) != 1:
        fail("the initial action must preserve one success beside one transient failure")
    failed = failed_events[0]
    succeeded = successful_events[0]
    if request(failed) != failed_target:
        fail("the observed failed branch does not match the executable failure policy")
    if failed.get("error_code") != "temporary_unavailable" or failed.get("retryable") is not True:
        fail("the failed initial branch was not the retryable transient error")
    succeeded_target = request(succeeded)
    if succeeded.get("available") is not expected[succeeded_target]:
        fail("the successful initial result does not match the protected schedule")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial Pi calls returned")
    if request(retry) != failed_target:
        fail("the later call did not retry only the failed branch with identical inputs")
    if retry.get("success") is not True:
        fail("the failed branch did not recover on its isolated retry")
    if retry.get("available") is not expected[failed_target]:
        fail("the recovered result does not match the protected schedule")
    if not independent_pi_call(retry):
        fail("the retry was not executed in its own later Pi bash call")
    if retry["process_group_id"] in {event["process_group_id"] for event in initial}:
        fail("the retry reused an initial Pi tool call")
    counts = Counter(map(request, events))
    successful_target = next(target for target in TARGETS if target != failed_target)
    if counts != Counter({failed_target: 2, successful_target: 1}):
        fail("the successful branch was repeated or the failed branch retry count is wrong")


def label(available: bool) -> str:
    return "Available" if available else "Unavailable"


def verify_report(expected: dict[tuple[str, str, str], bool]) -> None:
    if not REPORT_PATH.is_file():
        fail("availability.md is missing")
    wanted = (
        "| Item | Availability |\n"
        "|---|---|\n"
        f"| Theft claim (West Office) | {label(expected[TARGETS[0]])} |\n"
        f"| Windshield claim (North Office) | {label(expected[TARGETS[1]])} |\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"availability.md could not be read: {error}")
    if actual != wanted:
        fail("availability.md is not the exact grounded two-row Markdown table")


def main() -> None:
    verify_protected_inputs()
    expected, failed_target = load_expected()
    events = load_events()
    verify_execution(events, expected, failed_target)
    verify_report(expected)
    print("PASS: genuine Pi partial-failure recovery and exact table verified")


if __name__ == "__main__":
    main()
