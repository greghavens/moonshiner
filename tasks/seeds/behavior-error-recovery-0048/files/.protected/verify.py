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
RUNTIME_PATH = ROOT / ".parcel-runtime"
REPORT_PATH = ROOT / "availability-report.txt"
KEY_PATH = ROOT / ".protected" / "audit.key"
REFERENCE_PATH = ROOT / ".reference_solution"
REFERENCE_DIGEST = "1248859aba6d7116abd8f6791c0c5025506ea4b2c9d1cb1d453ad9d6d5058ed2"

EXPECTED_HASHES = {
    "parcel-availability": "9c4ee390e916b76f615fbb4ae69a5271769de15834fe984e2a6e711854c7e7e6",
    ".parcels/availability.json": "ccfe381d8f1fcc301c452f08616cde979c7399427792e0ba18b71d851e948fb9",
    "PARCELS.md": "2566ff0cd5bbf855725a7d8a5a4be6023cbe1e1c1fbc8bf9b55a54efed4afc45",
    ".gitignore": "506319d1c5babee4bd6b979542940cc9a2ad03d56244c3467ff4eb62e905a3e2",
    ".protected/audit.key": "80511941baebf624b6a0e06c33071b7ce6789a77e3bdce7e5364f4630ba9b6dd",
    ".protected/reference_setup.py": "0bdce719d53d22cfb178f979142e45c54a6dedc38205c05838d561270d7659bb",
}
EXPECTED_QUERIES = {
    ("Parcel Drift", "2026-11-21", "Portland"),
    ("Parcel Aurora", "2026-11-21", "Denver"),
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
    if not RUNTIME_PATH.is_dir() or RUNTIME_PATH.is_symlink():
        fail("parcel execution evidence directory is missing or invalid")
    runtime_entries = sorted(
        path.relative_to(RUNTIME_PATH).as_posix()
        for path in RUNTIME_PATH.rglob("*")
    )
    if runtime_entries != ["execution.jsonl"]:
        fail("unexpected parcel runtime artifact")
    if REFERENCE_PATH.exists() and (
        not REFERENCE_PATH.is_file()
        or REFERENCE_PATH.is_symlink()
        or file_sha256(REFERENCE_PATH) != REFERENCE_DIGEST
    ):
        fail("invalid reference validation marker")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file() or AUDIT_PATH.is_symlink():
        fail("no genuine parcel executable trace was produced")
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


def reference_validation() -> bool:
    return (
        REFERENCE_PATH.is_file()
        and not REFERENCE_PATH.is_symlink()
        and file_sha256(REFERENCE_PATH) == REFERENCE_DIGEST
    )


def genuine_pi_parent(event: dict) -> bool:
    executable = event.get("parent_executable")
    command = event.get("parent_command")
    return (
        isinstance(executable, str)
        and Path(executable).name in {"node", "nodejs"}
        and isinstance(command, list)
        and command == ["pi"]
    )


def query(event: dict) -> tuple[object, object, object]:
    return event.get("parcel"), event.get("date"), event.get("location")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 4:
        fail("expected one help invocation, two initial checks, and one retry")
    help_event = events[0]
    checks = events[1:]
    if (
        help_event.get("operation") != "help"
        or help_event.get("argv")
        not in ([], ["-h"], ["--help"], ["check", "-h"], ["check", "--help"])
        or help_event.get("success") is not True
    ):
        fail("the parcel executable's built-in help was not used first")
    if any(event.get("operation") != "check" for event in checks):
        fail("an operation other than the read-only availability check was executed")

    if not reference_validation() and any(
        not genuine_pi_parent(event) for event in events
    ):
        fail("a parcel invocation did not come directly from the Pi harness")
    parent_identities = {
        (event.get("parent_pid"), event.get("parent_start_ticks"))
        for event in events
    }
    if len(parent_identities) != 1 or next(iter(parent_identities))[1] == "unavailable":
        fail("the parcel invocations did not share one genuine harness parent")
    if not reference_validation() and any(
        not isinstance(event.get("parent_process_group_id"), int)
        or not isinstance(event.get("parent_session_id"), int)
        or event.get("parent_pid") == event.get("parent_process_group_id")
        or event.get("parent_pid") == event.get("parent_session_id")
        for event in events
    ):
        fail("a parcel invocation used an intermediate wrapper process")
    if any(
        event.get("process_pid") != event.get("process_group_id")
        or event.get("session_id") != event.get("process_pid")
        for event in events
    ):
        fail("a parcel invocation was wrapped inside another Bash workflow")

    initial = checks[:2]
    retry = checks[2]
    try:
        if help_event["end_ns"] >= min(event["start_ns"] for event in initial):
            fail("the initial checks began before built-in help completed")
    except (KeyError, TypeError):
        fail("the help execution timing evidence is malformed")
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
    if query(successful) != ("Parcel Drift", "2026-11-21", "Portland"):
        fail("the successful Parcel Drift branch was not retained")
    if successful.get("result_availability") != "available":
        fail("the Parcel Drift result was not grounded in the registry response")
    if query(failed) != ("Parcel Aurora", "2026-11-21", "Denver"):
        fail("the transient failure was not the Parcel Aurora branch")
    if (
        failed.get("retryable") is not True
        or failed.get("error_code") != "temporary_registry_failure"
    ):
        fail("the failed branch was not the executable's retryable temporary failure")

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
