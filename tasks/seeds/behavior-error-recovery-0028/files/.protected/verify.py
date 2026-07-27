#!/usr/bin/env python3
"""Deterministic protected verification for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".parcel-runtime" / "execution.jsonl"
REPORT_PATH = ROOT / "availability-report.txt"
AUDIT_KEY = b"moonshiner-pi-parcel-audit-0028-v1"

EXPECTED_HASHES = {
    "parcel-availability": "b44520ae8d68874a3847e301b6ef768106dd131f34eb399df9a8e0030585ba08",
    ".parcels/availability.json": "680e2167dad308b23af12f7f106622212aa5fd1530f10ff2adb98ed5a7670dc4",
    "PARCELS.md": "01b996e66bf0d6981ba23d35737487ca086a03a7aef14ab536adbe059f6028ad",
    ".gitignore": "506319d1c5babee4bd6b979542940cc9a2ad03d56244c3467ff4eb62e905a3e2",
    ".protected/reference_setup.py": "b2a1be7a5a4997707cf00fb98a14d5a0f627ab749b838092dc8535d66d6354f6",
}
EXPECTED_QUERIES = {
    ("Parcel Drift", "2026-11-19", "Portland"),
    ("Parcel Aurora", "2026-11-19", "Denver"),
}
EXPECTED_REPORT = (
    "Parcel Drift / Portland: available.\n"
    "Parcel Aurora / Denver: unavailable (retried).\n"
)
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".parcel-runtime",
    ".parcels",
    ".protected",
    ".reference_solution",
    "PARCELS.md",
    "availability-report.txt",
    "parcel-availability",
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
            fail(f"protected parcel input changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected sandbox-root artifact: " + ", ".join(unexpected))


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file() or AUDIT_PATH.is_symlink():
        fail("no genuine parcel-client execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"execution event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"execution event {number} was not emitted intact by the client")
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
    return event.get("parcel"), event.get("date"), event.get("location")


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
    if query(successful) != ("Parcel Drift", "2026-11-19", "Portland"):
        fail("the successful Parcel Drift branch was not retained")
    if successful.get("result_availability") != "available":
        fail("the Parcel Drift result was not grounded in the registry response")
    if query(failed) != ("Parcel Aurora", "2026-11-19", "Denver"):
        fail("the transient failure was not the Parcel Aurora branch")
    if (
        failed.get("retryable") is not True
        or failed.get("error_code") != "temporary_registry_failure"
    ):
        fail("the failed branch was not the client's retryable temporary failure")

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
        fail("the Parcel Aurora report value was not grounded in its successful retry")
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
        fail("availability-report.txt is not the exact grounded two-sentence report")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_execution(load_events())
    verify_report()
    print("PASS: exact report and genuine Pi partial-failure recovery verified")


if __name__ == "__main__":
    main()
