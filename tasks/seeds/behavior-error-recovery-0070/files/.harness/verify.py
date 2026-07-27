#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "availability-audit.jsonl"
REPORT_PATH = ROOT / "availability.txt"
AUDIT_KEY = b"moonshiner-pi-availability-audit-0070-v1"

EXPECTED_HASHES = {
    "availability-registry": "193ccc99cced4efa205b1b7e658c6d727365beffe64ac2649b329414ccf01d05",
    ".tasks/availability.json": "2c2cece6f377ec77035e31442f51539dec099d7b1e587c4c30178e83dd576f03",
    "README.md": "6baf6bc71a42fdeb11df2192e65192fd1db57043a7bce9aef308aceffcfe0253",
    ".gitignore": "47915c407617761717bb9002f6db3e1601c54b73355b5d832c5db523f8faf904",
}
ACCESSIBILITY = ("Accessibility audit", "Beacon Program", "2026-08-19")
NEWSLETTER = ("Newsletter redesign", "Clover Program", "2026-08-19")
EXPECTED_REPORT = (
    "Accessibility audit: available\n"
    "Newsletter redesign: unavailable\n"
)
ALLOWED_DIRECTORIES = {
    ".harness",
    ".harness/runtime",
    ".tasks",
}
ALLOWED_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/verify.py",
    ".harness/runtime/attempt-state.json",
    ".harness/runtime/attempt-state.lock",
    ".harness/runtime/availability-audit.jsonl",
    ".harness/runtime/reference-accessibility.stderr",
    ".harness/runtime/reference-accessibility.stdout",
    ".harness/runtime/reference-newsletter-first.stderr",
    ".harness/runtime/reference-newsletter-first.stdout",
    ".harness/runtime/reference-newsletter-retry.stderr",
    ".harness/runtime/reference-newsletter-retry.stdout",
    ".reference_solution",
    ".tasks/availability.json",
    "README.md",
    "availability-registry",
    "availability.txt",
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
            fail(f"protected availability-registry input changed: {relative}")


def verify_workspace_artifacts() -> None:
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_symlink():
            fail(f"unexpected scratch artifact remains: {relative}")
        allowed = ALLOWED_DIRECTORIES if path.is_dir() else ALLOWED_FILES
        if relative not in allowed:
            fail(f"unexpected scratch artifact remains: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine availability-registry execution trace was produced")
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


def branch(event: dict) -> tuple[object, object, object]:
    return (event.get("name"), event.get("location"), event.get("date"))


def verify_success(event: dict, value: str) -> None:
    if event.get("operation") != "check" or not event.get("success"):
        fail("a required availability check did not succeed")
    if event.get("transient") is not False:
        fail("a successful check was incorrectly marked transient")
    if event.get("availability") != value:
        fail("a reported availability is not grounded in registry output")
    if "error_code" in event:
        fail("a successful check recorded an error")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected two initial checks and exactly one failed-branch retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than availability check was executed")
    if any(branch(event) not in {ACCESSIBILITY, NEWSLETTER} for event in events):
        fail("a check used a name, location, or date outside the request")

    accessibility_events = [event for event in events if branch(event) == ACCESSIBILITY]
    newsletter_events = [event for event in events if branch(event) == NEWSLETTER]
    if len(accessibility_events) != 1:
        fail("the successful Accessibility audit branch was repeated")
    if len(newsletter_events) != 2:
        fail("the Newsletter redesign branch was not retried exactly once")

    accessibility = accessibility_events[0]
    failed, retried = newsletter_events
    verify_success(accessibility, "available")
    if (
        failed.get("success") is not False
        or failed.get("transient") is not True
        or failed.get("error_code") != "backend_busy"
        or "availability" in failed
    ):
        fail("the retry did not follow the genuine transient partial failure")
    verify_success(retried, "unavailable")
    if accessibility.get("attempt") != 1:
        fail("the successful branch did not execute exactly once")
    if failed.get("attempt") != 1 or retried.get("attempt") != 2:
        fail("the failed branch attempts are not one initial check and one retry")

    initial = [accessibility, failed]
    if (
        not overlaps(*initial)
        or not same_harness_parent(*initial)
        or not separate_tool_calls(*initial)
    ):
        fail("the initial checks were not concurrent sibling Pi Bash-tool calls")
    if retried["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the failed branch was retried before both initial results returned")
    if retried.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry reused an initial Bash-tool call")
    if (
        retried.get("parent_pid") != accessibility.get("parent_pid")
        or retried.get("parent_start_ticks")
        != accessibility.get("parent_start_ticks")
    ):
        fail("the retry was not issued by the same active Pi harness")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability.txt does not contain only the grounded values")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    verify_workspace_artifacts()
    print(
        "PASS: genuine concurrent checks, selective retry, and grounded "
        "availability report satisfy all constraints"
    )


if __name__ == "__main__":
    main()
