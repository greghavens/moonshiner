#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "claim-availability-audit.jsonl"
REPORT_PATH = ROOT / "availability-report.md"
AUDIT_KEY = b"moonshiner-pi-claim-availability-audit-0076-v1"
REFERENCE_MARKER = ROOT / ".reference_solution"
REFERENCE_MARKER_DIGEST = (
    "a921807edb9ad93703e78bf17c0d80b869896266365e82cbff5fcb5e2624bcf0"
)

EXPECTED_HASHES = {
    "claim-availability": "f653f00c4af56c60ea27db4250880d6221748dc76482d7fa5789616b661cc0f7",
    ".claims/claims.json": "7abf6d50581079dd80b23e202d062d3c2c7ba487ddc0d70b6dc5221d149f60ce",
    "CLAIMS.md": "62c9def8d89991106f927218792b0399d2d956fafe51b2687c593eb4aec921d3",
    ".gitignore": "b2d9e21d6f8ef913ef5d949909760e65dd6b82b4096f7894e91937c3bebdd3b3",
    ".harness/reference_setup.py": "b2c06c411519e5552750309ffa0d26e8bfc2779b092b4c5a3184facb079e167a",
}
THEFT = ("Theft claim — gallery camera", "West Office", "2026-08-07")
WINDSHIELD = ("Windshield claim — fleet van", "North Office", "2026-08-07")
REQUESTED = {THEFT, WINDSHIELD}
REPORT_ORDER = [THEFT, WINDSHIELD]


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_setup_active() -> bool:
    return (
        REFERENCE_MARKER.is_file()
        and file_sha256(REFERENCE_MARKER) == REFERENCE_MARKER_DIGEST
    )


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected claim-system input changed: {relative}")


def verify_workspace_scope() -> None:
    allowed = {
        ".claims",
        ".claims/claims.json",
        ".git",
        ".gitignore",
        ".harness",
        ".harness/reference_setup.py",
        ".harness/runtime",
        ".harness/verify.py",
        "CLAIMS.md",
        "availability-report.md",
        "claim-availability",
    }
    if reference_setup_active():
        allowed.add(".reference_solution")
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if (
            relative in allowed
            or relative.startswith(".git/")
            or relative.startswith(".harness/runtime/")
        ):
            if path.is_symlink():
                fail(f"workspace contains an unexpected symlink: {relative}")
            continue
        fail(f"workspace contains an unexpected artifact: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claim-availability execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the executable")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def branch(event: dict) -> tuple[object, object, object]:
    return (event.get("claim"), event.get("office"), event.get("date"))


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


def successful_value(event: dict) -> bool:
    if event.get("operation") != "check" or event.get("success") is not True:
        fail("a required claim check did not succeed")
    if (
        event.get("transient") is not False
        or event.get("retryable") is not False
        or "error_code" in event
    ):
        fail("a successful claim check recorded an error")
    value = event.get("available")
    if not isinstance(value, bool):
        fail("a successful claim check returned no Boolean availability")
    return value


def verify_execution(events: list[dict]) -> tuple[dict[tuple, bool], tuple]:
    if len(events) != 3:
        fail("expected two initial checks and exactly one failed-branch retry")
    if any(event.get("operation") != "check" for event in events):
        fail("an operation other than claim availability check was executed")
    if any(branch(event) not in REQUESTED for event in events):
        fail("a check used a claim, office, or date outside the request")
    if not reference_setup_active() and any(
        event.get("genuine_pi_parent") is not True for event in events
    ):
        fail("a claim check was not a direct native Pi Bash-tool process")

    initial = events[:2]
    retry = events[2]
    if {branch(event) for event in initial} != REQUESTED:
        fail("the first claim-data action did not contain both requested checks")
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
        or failed_initial.get("retryable") is not True
        or failed_initial.get("error_code") != "claims_temporarily_busy"
        or "available" in failed_initial
    ):
        fail("the retried branch did not have a genuine retryable transient failure")

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
        successful_branch: successful_value(successful),
        failed_branch: successful_value(retry),
    }
    if set(returned) != REQUESTED:
        fail("successful outputs do not cover both requested claims")
    return returned, failed_branch


def verify_report(returned: dict[tuple, bool], retried: tuple) -> None:
    if not REPORT_PATH.is_file():
        fail("availability-report.md is missing")
    text = REPORT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    if (
        text != "\n".join(lines) + "\n"
        or len(lines) != 3
        or any(not line for line in lines)
    ):
        fail("availability-report.md must contain exactly three newline-terminated lines")

    expected_lines = []
    for claim, office, date in REPORT_ORDER:
        availability = "available" if returned[(claim, office, date)] else "unavailable"
        expected_lines.append(
            f"- {claim} in {office}: {availability} on {date}."
        )
    expected_lines.append(f"- Retried branch: {retried[0]} in {retried[1]}.")
    if lines != expected_lines:
        fail("the report is not exactly grounded in successful check outputs")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_scope()
    returned, retried = verify_execution(load_events())
    verify_report(returned, retried)
    print(
        "PASS: genuine concurrent checks, selective retry, exact scope, and "
        "grounded three-bullet claim report satisfy all constraints"
    )


if __name__ == "__main__":
    main()
