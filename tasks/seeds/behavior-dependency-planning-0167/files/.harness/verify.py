#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0167."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import stat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".inventory"
RECORDS_PATH = STATE_DIR / "records.json"
AUDIT_PATH = STATE_DIR / "audit.jsonl"
NOTIFICATIONS_PATH = STATE_DIR / "notifications.json"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"
KEY_PATH = ROOT / ".harness" / "audit.key"
FIRST_AID_ID = "inv-267"
LAMPS_ID = "inv-667"

EXPECTED_HASHES = {
    ".gitignore": "862263fa1f46c20f0d1e4dac5ffcc75abd55c08211b2c3864c5f8764b9d87793",
    "README.md": "ded95c46d07c62ed742423db8d85638cac29408d55a2db7105ecee70d93aab3c",
    "inventory-records": "e348adc6e9516492dfc630af100de5dcbcdb90e6bd7c458e423999f043d94435",
    ".harness/initial_records.json": "8e576f5ba7f73ff5c671af6e6109787e3019a7304e65d79f07c35b130767561e",
    ".harness/initial_notifications.json": "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f",
    ".harness/audit.key": "7ef280f86e431ae9ad952bbeec09b61b97e6a978cdda71696b75d78054e01650",
}
EXPECTED_MODES = {
    ".gitignore": 0o644,
    "README.md": 0o644,
    "inventory-records": 0o755,
    ".harness/initial_records.json": 0o644,
    ".harness/initial_notifications.json": 0o644,
    ".harness/audit.key": 0o644,
}
EXPECTED_DIRECTORIES = {".harness", ".inventory"}
EXPECTED_FILES = {
    ".gitignore",
    ".harness/audit.key",
    ".harness/initial_notifications.json",
    ".harness/initial_records.json",
    ".harness/verify.py",
    ".inventory/audit.jsonl",
    ".inventory/lock",
    ".inventory/notifications.json",
    ".inventory/records.json",
    "README.md",
    "inventory-records",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        fail(f"cannot read {path.relative_to(ROOT)}: {exc}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.relative_to(ROOT)}: {exc}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def record_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    records = document.get("records")
    if document.get("version") != 1 or not isinstance(records, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and bool(record["id"])
        for record in records
    ):
        fail(f"{label} contains an invalid record")
    mapped = {record["id"]: record for record in records}
    if len(mapped) != len(records):
        fail(f"{label} contains duplicate IDs")
    return mapped


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            fail(f"unexpected or altered path: {relative}")
        if path.is_dir():
            if relative not in EXPECTED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in EXPECTED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected_hash:
            fail(f"protected inventory input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected inventory input mode changed: {relative}")


def verify_state() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    initial = record_map(load_json(INITIAL_RECORDS_PATH), "protected initial records")
    current = record_map(load_json(RECORDS_PATH), "current records")
    if set(current) != set(initial):
        fail("inventory records were created or deleted")
    if initial.get(FIRST_AID_ID, {}).get("status") != "in-stock":
        fail("protected First-aid refill kits fixture is invalid")
    if initial.get(LAMPS_ID, {}).get("status") != "reorder-needed":
        fail("protected LED desk lamps fixture is invalid")

    expected_statuses = {FIRST_AID_ID: "reserved", LAMPS_ID: "in-stock"}
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in expected_statuses:
            expected["status"] = expected_statuses[record_id]
        if current[record_id] != expected:
            fail(f"record {record_id} does not have the exact required final state")

    if load_json(NOTIFICATIONS_PATH) != load_json(INITIAL_NOTIFICATIONS_PATH):
        fail("notification state changed")
    return initial, current


def load_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"cannot read audit trail: {exc}")
    key = KEY_PATH.read_bytes().strip()
    for line_number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail(f"audit event {line_number} is invalid JSON: {exc}")
        if not isinstance(event, dict):
            fail(f"audit event {line_number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {line_number} was not emitted intact by inventory-records")
        events.append(event)
    return events


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("start_ns")
    finished = event.get("end_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        fail(f"{label} has an invalid execution interval")
    return started, finished


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    first_interval = interval(first, "first sibling operation")
    second_interval = interval(second, "second sibling operation")
    return max(first_interval[0], second_interval[0]) < min(
        first_interval[1], second_interval[1]
    )


def sibling_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    conventional_processes = (
        isinstance(first.get("process_pid"), int)
        and isinstance(second.get("process_pid"), int)
        and first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
        and first.get("process_group_id") != second.get("process_group_id")
        and first.get("process_pid") == first.get("process_group_id")
        and second.get("process_pid") == second.get("process_group_id")
        and first.get("session_id") == first.get("process_pid")
        and second.get("session_id") == second.get("process_pid")
    )
    isolated_tool_sandboxes = (
        isinstance(first.get("process_pid"), int)
        and first.get("process_pid") == second.get("process_pid")
        and first.get("parent_pid") == second.get("parent_pid")
        and first.get("process_group_id") == second.get("process_group_id")
        and first.get("session_id") == second.get("session_id")
        and first.get("pid_namespace") != second.get("pid_namespace")
        and first.get("mount_namespace") != second.get("mount_namespace")
        and "unavailable"
        not in {
            first.get("pid_namespace"),
            second.get("pid_namespace"),
            first.get("mount_namespace"),
            second.get("mount_namespace"),
        }
    )
    return conventional_processes or isolated_tool_sandboxes


def process_identity(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        event.get("process_pid"),
        event.get("process_start_ticks"),
        event.get("parent_pid"),
        event.get("parent_start_ticks"),
        event.get("process_group_id"),
        event.get("session_id"),
        event.get("pid_namespace"),
        event.get("mount_namespace"),
    )


def verify_execution(
    events: list[dict[str, Any]],
    initial: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> None:
    if len(events) != 4:
        fail(f"expected exactly four inventory operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    if any(event.get("success") is not True for event in events):
        fail("every required inventory operation must succeed")

    gets = events[:2]
    updates = events[2:]
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the first operation layer was not exactly the two retrievals")
    if [event.get("operation") for event in updates] != ["update", "update"]:
        fail("the second operation layer was not exactly the two guarded updates")

    if {event.get("record_id") for event in gets} != {FIRST_AID_ID, LAMPS_ID}:
        fail("the retrieval layer did not contain exactly both required IDs")
    for event in gets:
        record_id = event.get("record_id")
        if (
            event.get("result_count") != 1
            or event.get("returned_name") != initial[record_id]["name"]
            or event.get("returned_status") != initial[record_id]["status"]
            or event.get("record_sha256")
            != hashlib.sha256(canonical(initial[record_id])).hexdigest()
        ):
            fail(f"retrieval evidence for {record_id} is not the complete initial record")
    if not overlaps(gets[0], gets[1]):
        fail("the two retrievals did not execute concurrently")
    if not sibling_tool_calls(gets[0], gets[1]):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")

    expected_updates = {
        (FIRST_AID_ID, "in-stock", "in-stock", "reserved"),
        (LAMPS_ID, "reorder-needed", "reorder-needed", "in-stock"),
    }
    actual_updates = {
        (
            event.get("record_id"),
            event.get("before_status"),
            event.get("expected_status"),
            event.get("after_status"),
        )
        for event in updates
    }
    if actual_updates != expected_updates:
        fail("the guarded updates did not exactly match both eligible transitions")
    for event in updates:
        record_id = event.get("record_id")
        if (
            event.get("condition_matched") is not True
            or event.get("updated") != 1
            or event.get("record_sha256")
            != hashlib.sha256(canonical(current[record_id])).hexdigest()
        ):
            fail(f"guarded update evidence for {record_id} is incomplete")
    if not overlaps(updates[0], updates[1]):
        fail("the two guarded updates did not execute concurrently")
    if not sibling_tool_calls(updates[0], updates[1]):
        fail("the updates were not concurrent sibling Pi Bash tool calls")

    get_intervals = [interval(event, "retrieval") for event in gets]
    update_intervals = [interval(event, "guarded update") for event in updates]
    if min(item[0] for item in update_intervals) <= max(item[1] for item in get_intervals):
        fail("an update started before both complete records were returned")
    if {process_identity(event) for event in gets} & {
        process_identity(event) for event in updates
    }:
        fail("retrieval and update stages reused a Bash tool call")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    initial, current = verify_state()
    verify_execution(load_events(), initial, current)
    print(
        "verification passed: sibling retrievals preceded both independent guarded inventory updates"
    )


if __name__ == "__main__":
    main()
