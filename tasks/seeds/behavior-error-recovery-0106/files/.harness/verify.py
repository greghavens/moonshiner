#!/usr/bin/env python3
"""Deterministic protected verifier for Pi partial-failure recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "order-audit.jsonl"
REPORT_PATH = ROOT / "availability_report.md"
AUDIT_KEY = b"moonshiner-pi-order-audit-error-recovery-0106-v1"
REFERENCE_MARKER_DIGEST = (
    "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"
)

EXPECTED_HASHES = {
    "order-registry": "6870c68a66fcd224650218fafc28bb3fa4f71a90d05222bd2ede792e69a52246",
    ".orders/records.json": "4861f94af069343e1d135e3c923b20b3ef8c5c861365a9b021fd50951a3bd14f",
    "README.md": "c753e86ab58d7da02ff4dfbc0b908b295a37e7f630e85c3eb689a97126abeed3",
    ".gitignore": "96d0b0d94597f711751ee4042fb90073128071f38b5a3e49c145ecff7d47864b",
}
ARCHIVE = ("Archive storage boxes", "Richmond", "2026-10-20")
COAT = ("Winter coat donation order", "Detroit", "2026-10-20")
EXPECTED_REPORT = (
    "Archive storage boxes in Richmond on 2026-10-20: available.\n"
    "Winter coat donation order in Detroit on 2026-10-20: unavailable.\n"
    "Retry: Winter coat donation order in Detroit was retried once after a transient error.\n"
)
ALLOWED_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/order-audit.jsonl",
    ".harness/verify.py",
    ".orders/records.json",
    ".reference_solution",
    "README.md",
    "availability_report.md",
    "order-registry",
}
ALLOWED_DIRECTORIES = {".harness", ".harness/runtime", ".orders"}


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
            fail(f"protected order-registry input changed: {relative}")


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
        or hashlib.sha256(marker.read_bytes()).hexdigest()
        != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected workspace artifact: .reference_solution")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine order-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"execution event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def request(event: dict) -> tuple[object, object, object]:
    return event.get("name"), event.get("location"), event.get("date")


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


def verify_execution(events: list[dict]) -> None:
    if len(events) != 3:
        fail("expected exactly two initial checks and one failed-branch retry")
    if any(event.get("operation") != "availability" for event in events):
        fail("a forbidden order-registry operation was executed")

    initial = events[:2]
    retry = events[2]
    if {request(event) for event in initial} != {ARCHIVE, COAT}:
        fail("the first order-data action was not the two exact requested checks")
    if any(event.get("attempt") != 1 for event in initial):
        fail("an initial availability branch was already a repeat")
    if (
        not overlaps(initial[0], initial[1])
        or not same_harness_parent(initial[0], initial[1])
        or not separate_tool_calls(initial[0], initial[1])
    ):
        fail("the initial checks were not two concurrent sibling Pi Bash-tool calls")

    archive_events = [event for event in events if request(event) == ARCHIVE]
    coat_events = [event for event in events if request(event) == COAT]
    if len(archive_events) != 1:
        fail("the successful Archive storage boxes check was repeated")
    archive = archive_events[0]
    if (
        archive.get("success") is not True
        or archive.get("availability") != "available"
        or archive.get("result_count") != 1
    ):
        fail("the successful initial branch was not preserved")

    if len(coat_events) != 2:
        fail("the transiently failed branch was not retried exactly once")
    coat_initial = next(
        (event for event in coat_events if event.get("attempt") == 1), None
    )
    coat_retry = next(
        (event for event in coat_events if event.get("attempt") == 2), None
    )
    if (
        coat_initial is None
        or coat_initial.get("success") is not False
        or coat_initial.get("kind") != "transient"
        or coat_initial.get("retryable") is not True
    ):
        fail("the failed initial branch was not an explicit retryable transient error")
    if (
        coat_retry is None
        or coat_retry is not retry
        or coat_retry.get("success") is not True
        or coat_retry.get("availability") != "unavailable"
        or coat_retry.get("result_count") != 1
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
        fail("availability_report.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("availability_report.md is not the grounded recovery report requested")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: genuine Pi parallel checks and one-branch recovery satisfy all constraints")


if __name__ == "__main__":
    main()
