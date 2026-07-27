#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi partial-failure task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / ".claims" / "availability.json"
AUDIT_PATH = ROOT / ".harness" / "runtime" / "availability-audit.jsonl"
REPORT_PATH = ROOT / "availability.md"
AUDIT_KEY = b"moonshiner-pi-insurance-recovery-0016-v1"
REFERENCE_MARKER_DIGEST = "d50e64a94d3ce6d17dac7bbcab07d23c9ded2372f05109ba52e4981c741c4a2a"

EXPECTED_HASHES = {
    "claim-availability": "8a6a2159dfa037cf08ae181ddd8bc6334c783ec6381fd0e26f4eb29d2a364916",
    ".claims/availability.json": "ec16f4fad11468ff3c7126ad72920e05bbb33a4a5c6b8299dc5a8420d5c3b456",
    ".harness/reference_setup.py": "641eb299cdc5c54033285676eb3893d5a709d4835d83023839359e8aae5741b7",
    "README.md": "31d25dba6d409fc56094249ac0ec251618d422bb6751f00d1fe44cafbaac33ff",
    ".gitignore": "1ca1d15c40d36f49707290d06a2a0afd851f0bf5f9282ff20b2452e2c78b62ed",
}
ALLOWED_FILES = {
    ".claims/availability.json",
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/availability-audit.jsonl",
    ".harness/runtime/initial-arrivals.jsonl",
    ".harness/runtime/north-attempts",
    ".harness/verify.py",
    ".reference_solution",
    "README.md",
    "availability.md",
    "claim-availability",
}
THEFT = ("2026-11-25", "Theft claim", "West Office")
WINDSHIELD = ("2026-11-25", "Windshield claim", "North Office")


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
            fail(f"protected insurance-registry input changed: {relative}")


def verify_workspace_files() -> None:
    actual_files: set[str] = set()
    for path in ROOT.rglob("*"):
        if path.is_dir() and not path.is_symlink():
            continue
        relative = path.relative_to(ROOT).as_posix()
        # The seed runner applies the candidate patch in a temporary Git
        # worktree.  Its control files (for example, COMMIT_EDITMSG) are
        # runner-owned metadata rather than sandbox output from the agent.
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            fail(f"sandbox entry must not be a symbolic link: {relative}")
        actual_files.add(relative)
    unexpected = sorted(actual_files - ALLOWED_FILES)
    if unexpected:
        fail(f"unexpected sandbox entry created: {unexpected[0]}")
    marker = ROOT / ".reference_solution"
    if marker.is_file() and file_sha256(marker) != REFERENCE_MARKER_DIGEST:
        fail("unexpected sandbox entry created: .reference_solution")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine availability-check execution trace was produced")
    events: list[dict] = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the executable")
        events.append(event)
    return sorted(events, key=lambda event: event.get("start_ns", -1))


def target(event: dict) -> tuple[object, object, object]:
    return (event.get("date"), event.get("item"), event.get("office"))


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


def verify_execution(events: list[dict]) -> dict[tuple[str, str, str], str]:
    help_events = [event for event in events if event.get("operation") == "help"]
    check_events = [event for event in events if event.get("operation") == "check"]
    if len(check_events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if len(help_events) + len(check_events) != len(events):
        fail("an unsupported registry operation was executed")
    if not help_events or events[0].get("operation") != "help":
        fail("the built-in top-level help was not called before checking")
    if any(
        event.get("success") is not True
        or any(
            field in event
            for field in ("date", "item", "office", "attempt", "availability", "error")
        )
        for event in help_events
    ):
        fail("the built-in top-level help did not complete successfully")
    if not any(
        help_event.get("end_ns", -1)
        < min(event.get("start_ns", -1) for event in check_events)
        for help_event in help_events
    ):
        fail("the built-in help call did not finish before availability checking began")

    events = check_events

    initial = events[:2]
    retry = events[2]
    if {target(event) for event in initial} != {THEFT, WINDSHIELD}:
        fail("the first action did not contain exactly both requested checks")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not concurrent sibling Pi Bash-tool calls")

    by_target = {target(event): event for event in initial}
    theft = by_target[THEFT]
    windshield = by_target[WINDSHIELD]
    if (
        theft.get("attempt") != 1
        or theft.get("success") is not True
        or theft.get("retryable") is not False
        or not isinstance(theft.get("availability"), str)
        or not theft["availability"]
    ):
        fail("the successful Theft claim branch was not preserved intact")
    if (
        windshield.get("attempt") != 1
        or windshield.get("success") is not False
        or windshield.get("retryable") is not True
        or windshield.get("error") != "temporary_failure"
        or "availability" in windshield
    ):
        fail("the initial Windshield claim branch was not the retryable failure")

    if target(retry) != WINDSHIELD:
        fail("the retry repeated or replaced the wrong branch")
    if retry.get("start_ns", -1) <= max(event["end_ns"] for event in initial):
        fail("the failed branch was retried before both initial results returned")
    if (
        retry.get("attempt") != 2
        or retry.get("success") is not True
        or retry.get("retryable") is not False
        or not isinstance(retry.get("availability"), str)
        or not retry["availability"]
    ):
        fail("the failed Windshield claim branch was not retried successfully once")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry was not issued as a later Pi Bash-tool call")

    return {THEFT: theft["availability"], WINDSHIELD: retry["availability"]}


def grounded_availability(target_value: tuple[str, str, str]) -> str:
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"protected availability data is unreadable: {exc}")
    matches = [
        entry
        for entry in payload.get("entries", [])
        if (entry.get("date"), entry.get("item"), entry.get("office")) == target_value
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("availability"), str):
        fail("protected availability data does not resolve an exact target uniquely")
    return matches[0]["availability"]


def verify_report(results: dict[tuple[str, str, str], str]) -> None:
    if results.get(THEFT) != grounded_availability(THEFT):
        fail("the successful Theft claim result does not match the registry")
    if results.get(WINDSHIELD) != grounded_availability(WINDSHIELD):
        fail("the retried Windshield claim result does not match the registry")
    expected = (
        "| Item | Availability |\n"
        "| --- | --- |\n"
        f"| Theft claim | {results[THEFT]} |\n"
        f"| Windshield claim | {results[WINDSHIELD]} |\n"
    )
    if not REPORT_PATH.is_file():
        fail("availability.md is missing")
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        fail("availability.md must be UTF-8 text")
    if actual != expected:
        fail("availability.md is not exactly the requested grounded two-row table")


def main() -> None:
    verify_protected_inputs()
    results = verify_execution(load_events())
    verify_report(results)
    verify_workspace_files()
    print("PASS: genuine parallel checks, failed-branch-only retry, and exact table verified")


if __name__ == "__main__":
    main()
