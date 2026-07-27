#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "program-audit.jsonl"
REPORT_PATH = ROOT / "task_availability.md"
AUDIT_KEY = b"moonshiner-pi-program-audit-error-recovery-0110-v1"
REFERENCE_MARKER_DIGEST = "195b6ca275fb06a64959ae411823f718a9d1e42930ee65befa44fbb183a16a15"

EXPECTED_HASHES = {
    "program-board": "da37c2f7e226c54e0a284737e5e46a9a2aa12de186d76f879a136aa7bc3da01a",
    ".programs/records.json": "d8a4d902ec753130a3f36d2cab1fb090d831c3395f32ee917ed0e3e1beaa6fc5",
    "README.md": "48dbce9decf4633bc493bca1ef3ac317f1166d35e77276a466e4eddef89fea6f",
    ".gitignore": "0d5aed8ebb688da6793d2fb17b3da8a49a9c20345d68428be156bf4a96224ae5",
    ".protected/reference_setup.py": "e0e2115f7d8547cfe4f431d131b2b3fdf2ad3d0834185167ca68a29133ec819e",
}
VOLUNTEER = ("Volunteer handbook refresh", "Community Program", "2026-10-29")
FLEET = ("Fleet electrification study", "Transit Program", "2026-10-29")
EXPECTED_REPORT = (
    "- Volunteer handbook refresh: available\n"
    "- Fleet electrification study: unavailable\n"
)
ALLOWED_FILES = {
    ".gitignore",
    ".programs/records.json",
    ".protected/reference_setup.py",
    ".protected/runtime/program-audit.jsonl",
    ".protected/verify.py",
    ".reference_solution",
    "README.md",
    "program-board",
    "task_availability.md",
}
ALLOWED_DIRECTORIES = {".programs", ".protected", ".protected/runtime"}


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
            fail(f"protected program-board input changed: {relative}")


def verify_workspace_shape() -> None:
    paths = []
    for top_level in ROOT.iterdir():
        if top_level.name == ".git":
            continue
        paths.append(top_level)
        if top_level.is_dir():
            paths.extend(top_level.rglob("*"))

    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        allowed = relative in (
            ALLOWED_DIRECTORIES if path.is_dir() else ALLOWED_FILES
        )
        if not allowed:
            fail(f"unexpected workspace artifact: {relative}")

    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file()
        or file_sha256(marker) != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected workspace artifact: .reference_solution")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine program-board execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"execution event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"execution event {number} was not emitted intact by the program board")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def request(event: dict) -> tuple[object, object, object]:
    return event.get("task"), event.get("program"), event.get("date")


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


def verify_event_shape(event: dict, number: int) -> None:
    for field in (
        "start_ns",
        "end_ns",
        "process_pid",
        "process_group_id",
        "session_id",
        "parent_pid",
        "attempt",
    ):
        if not isinstance(event.get(field), int) or isinstance(event.get(field), bool):
            fail(f"execution event {number} has invalid {field} evidence")
    for field in ("process_start_ticks", "parent_start_ticks"):
        if not isinstance(event.get(field), str):
            fail(f"execution event {number} has invalid {field} evidence")
    if event["start_ns"] >= event["end_ns"]:
        fail(f"execution event {number} has an invalid interval")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    for number, event in enumerate(events, 1):
        verify_event_shape(event, number)
    if any(event.get("operation") != "availability" for event in events):
        fail("a forbidden program-board operation was executed")

    initial = events[:2]
    retry = events[2]
    if {request(event) for event in initial} != {VOLUNTEER, FLEET}:
        fail("the first program-data action was not the two exact requested checks")
    if any(event.get("attempt") != 1 for event in initial):
        fail("an initial availability branch was already a repeat")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not two concurrent sibling Pi Bash-tool calls")

    volunteer_events = [event for event in events if request(event) == VOLUNTEER]
    fleet_events = [event for event in events if request(event) == FLEET]
    if len(volunteer_events) != 1:
        fail("the successful Volunteer handbook refresh check was repeated")
    volunteer = volunteer_events[0]
    if (
        volunteer.get("success") is not True
        or volunteer.get("availability") != "available"
        or volunteer.get("result_count") != 1
    ):
        fail("the successful initial branch was not retained")

    if len(fleet_events) != 2:
        fail("the transiently failed branch was not retried exactly once")
    fleet_initial = next(
        (event for event in fleet_events if event.get("attempt") == 1), None
    )
    fleet_retry = next(
        (event for event in fleet_events if event.get("attempt") == 2), None
    )
    if (
        fleet_initial is None
        or fleet_initial.get("success") is not False
        or fleet_initial.get("kind") != "transient"
        or fleet_initial.get("retryable") is not True
    ):
        fail("the failed initial branch was not an explicit retryable transient error")
    if (
        fleet_retry is None
        or fleet_retry is not retry
        or fleet_retry.get("success") is not True
        or fleet_retry.get("availability") != "unavailable"
        or fleet_retry.get("result_count") != 1
    ):
        fail("the one allowed failed-branch retry did not succeed")

    if retry["start_ns"] <= max(event["end_ns"] for event in initial):
        fail("the retry began before both initial results were available")
    if not same_harness_parent(initial[0], retry):
        fail("the retry was not executed by the same genuine Pi harness run")
    if retry.get("process_group_id") in {
        event.get("process_group_id") for event in initial
    }:
        fail("the retry reused an initial Bash-tool call")
    if (
        retry.get("process_pid") != retry.get("process_group_id")
        or retry.get("session_id") != retry.get("process_pid")
    ):
        fail("the retry was not a separate direct Pi Bash-tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("task_availability.md is missing")
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        fail(f"task_availability.md is not readable UTF-8: {exc}")
    if actual != EXPECTED_REPORT:
        fail("task_availability.md is not the exact grounded two-bullet report")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: genuine Pi parallel checks and selective recovery satisfy all constraints")


if __name__ == "__main__":
    main()
