#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi library reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / ".library" / "records.json"
AUDIT_PATH = ROOT / ".harness" / "runtime" / "library-audit.jsonl"
AUDIT_KEY = b"moonshiner-pi-library-audit-0153-v1"

EXPECTED_HASHES = {
    "library-records": "4cd720f2527f1bffc7769fa4e8f387ac4612ef3e3daa7893d5a9f092c47b9e27",
    "LIBRARY_RECORDS.md": "a4d65bab49e5986bce96684940e5112b06e30d5a459f8332aa4757d368eaf763",
    ".gitignore": "466c256f59227c7bae99d521a03342f49edac66d0a4f06875763e2a4996ecbb5",
    ".library/records.lock": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}
EXPECTED_RECORDS = [
    {
        "id": "lib-253",
        "location": "Bookmobile",
        "name": "Moonlight over Alder Bay",
        "status": "on-hold",
    },
    {
        "id": "lib-653",
        "location": "Central Branch",
        "name": "A Short History of Type",
        "status": "available",
    },
    {
        "id": "lib-1053",
        "location": "East Branch",
        "name": "Moonlight over Alder Bay archive",
        "status": "closed",
    },
]
EXPECTED_GET_STATUSES = {"lib-253": "available", "lib-653": "on-loan"}
EXPECTED_UPDATES = {
    "lib-253": ("available", "on-hold"),
    "lib-653": ("on-loan", "available"),
}
FORBIDDEN_OPERATIONS = {"search", "cancel", "create", "notify"}


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
            fail(f"protected library-registry input changed: {relative}")


def load_payload() -> dict:
    try:
        with DATA_PATH.open(encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read final library state: {exc}")
    if payload.get("records") != EXPECTED_RECORDS:
        fail("library record state differs from the required conditional updates")
    if payload.get("notifications") != []:
        fail("a notification was created")
    if set(payload) != {"records", "notifications"}:
        fail("the library registry shape changed")
    return payload


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine library-registry execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


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


def require_sibling_stage(events: list[dict], label: str) -> None:
    if (
        not overlaps(events[0], events[1])
        or not same_harness_parent(events[0], events[1])
        or not separate_tool_calls(events[0], events[1])
    ):
        fail(f"the {label} were not two concurrent sibling Pi Bash-tool calls")


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a prohibited library-registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two retrievals followed by exactly two updates")
    if any(event.get("success") is not True for event in events):
        fail("all required library-registry operations must succeed")

    gets, updates = events[:2], events[2:]
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the first library-data action must contain only both retrievals")
    if [event.get("operation") for event in updates] != ["update", "update"]:
        fail("the next library-data action must contain only both conditional updates")
    if {event.get("stable_id") for event in gets} != set(EXPECTED_GET_STATUSES):
        fail("the two required records were not retrieved exactly once")
    for event in gets:
        stable_id = event.get("stable_id")
        result = event.get("result_record")
        if event.get("result_count") != 1 or not isinstance(result, dict):
            fail(f"retrieval did not return one complete record: {stable_id}")
        if result.get("id") != stable_id:
            fail(f"retrieval evidence has the wrong stable ID: {stable_id}")
        if result.get("status") != EXPECTED_GET_STATUSES.get(stable_id):
            fail(f"retrieval did not establish the required starting status: {stable_id}")
    require_sibling_stage(gets, "record retrievals")

    if min(event["start_ns"] for event in updates) <= max(
        event["end_ns"] for event in gets
    ):
        fail("an update began before both current records were returned")
    if {event.get("stable_id") for event in updates} != set(EXPECTED_UPDATES):
        fail("the conditional updates did not target exactly the retrieved records")
    for event in updates:
        stable_id = event.get("stable_id")
        expected_from, expected_to = EXPECTED_UPDATES[stable_id]
        if event.get("if_status") != expected_from:
            fail(f"update did not preserve the retrieved status precondition: {stable_id}")
        if event.get("requested_status") != expected_to:
            fail(f"update requested the wrong target status: {stable_id}")
        if event.get("previous_status") != expected_from:
            fail(f"update observed a different starting status: {stable_id}")
        if event.get("applied") is not True or event.get("result_status") != expected_to:
            fail(f"conditional update was not applied as requested: {stable_id}")
    require_sibling_stage(updates, "conditional updates")

    get_groups = {event["process_group_id"] for event in gets}
    update_groups = {event["process_group_id"] for event in updates}
    if get_groups & update_groups:
        fail("retrieval and update stages reused a Bash-tool call")


def main() -> None:
    verify_protected_inputs()
    load_payload()
    verify_execution(load_events())
    print("PASS: genuine Pi retrievals and conditional updates satisfy every constraint")


if __name__ == "__main__":
    main()
