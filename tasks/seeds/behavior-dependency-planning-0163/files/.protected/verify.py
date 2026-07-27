#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0163."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".hospitality"
INITIAL_RECORDS = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS = ROOT / ".protected" / "initial_notifications.json"
INITIAL_AUDIT = ROOT / ".protected" / "initial_audit.jsonl"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
EXECUTABLE = ROOT / "hospitalityctl"
DOCUMENTATION = ROOT / "HOSPITALITY.md"

EXPECTED_EXECUTABLE_SHA256 = (
    "f06a63d0b6c6be516cfcb8e6e99843d09e0a5b55e981aae675c14c5852aafb9b"
)
EXPECTED_DOCUMENTATION_SHA256 = (
    "8865007452961b5ba98720e6058049420275fd5c62a2b6fdc9e7f1301811f5f2"
)
EXPECTED_INITIAL_RECORDS_SHA256 = (
    "cd9bde871f25d50cbd6b59a37daec6e602a9a5dcc14708a225ec17c0770fae73"
)
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = (
    "ddcad46235b2a83f71360b59dd92fb85b97dc03ab563f4e49d0a0a3e403d1936"
)
EXPECTED_INITIAL_AUDIT_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
EXPECTED_AUDIT_KEY_SHA256 = (
    "98d75e8117a4063e5016e5ce4ec1a9ec311379b04e39b911bc7eda6b5c96cc64"
)

ATRIUM_ID = "hos-263"
WILLOW_ID = "hos-663"
TARGETS = {
    ATRIUM_ID: ("Atrium breakfast", "confirmed", "completed"),
    WILLOW_ID: ("Willow Ballroom reception", "held", "confirmed"),
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def require_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} is not a JSON object")
    return value


def record_map(
    document: dict[str, Any], label: str
) -> dict[str, dict[str, Any]]:
    values = document.get("records")
    if document.get("version") != 1 or not isinstance(values, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and bool(value["id"])
        for value in values
    ):
        fail(f"{label} contains an invalid record")
    mapped = {value["id"]: value for value in values}
    if len(mapped) != len(values):
        fail(f"{label} contains a duplicate ID")
    return mapped


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with (STATE / "audit.jsonl").open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"execution-journal line {line_number} is invalid: {error}")
                if not isinstance(event, dict):
                    fail(f"execution-journal line {line_number} is not an object")
                events.append(event)
    except OSError as error:
        fail(f"cannot read the execution journal: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = AUDIT_KEY.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"execution event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"execution event {index} has an invalid seal")


def execution_interval(
    event: dict[str, Any], label: str
) -> tuple[int, int]:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        fail(f"{label} has an invalid execution interval")
    return started, finished


def intervals_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(
        load_object(STATE / "records.json", "current records"), "current records"
    )
    if set(current) != set(initial):
        fail("records were created or deleted")

    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in TARGETS:
            name, starting, final = TARGETS[record_id]
            if original.get("name") != name or original.get("status") != starting:
                fail(f"protected target fixture {record_id} is invalid")
            expected["status"] = final
        if current[record_id] != expected:
            if record_id in TARGETS:
                fail(f"target record {record_id} was not changed exactly as required")
            fail(f"untargeted record {record_id} was modified")

    initial_notifications = load_object(
        INITIAL_NOTIFICATIONS, "protected initial notifications"
    )
    current_notifications = load_object(
        STATE / "notifications.json", "current notifications"
    )
    if current_notifications != initial_notifications:
        fail("notification state changed")


def verify_retrievals(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    actual: dict[Any, tuple[Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        if event.get("operation") != "get" or event.get("outcome") != "ok":
            fail("the first dependency layer was not exactly two successful retrievals")
        record_id = event.get("record_id")
        if record_id in actual:
            fail("a required complete-record retrieval was duplicated")
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        actual[record_id] = (event.get("record_sha256"), event.get("status"))
        intervals.append(execution_interval(event, f"retrieval event {index}"))

    expected = {
        record_id: (record_digest(initial[record_id]), starting)
        for record_id, (_name, starting, _final) in TARGETS.items()
    }
    if actual != expected:
        fail("retrievals did not return both exact initial target records")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")
    return intervals


def verify_updates(
    events: list[dict[str, Any]], retrieval_intervals: list[tuple[int, int]]
) -> None:
    actual: dict[Any, tuple[Any, Any, Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("operation") != "update" or event.get("outcome") != "ok":
            fail("the second dependency layer was not exactly two successful updates")
        record_id = event.get("record_id")
        if record_id in actual:
            fail("a required conditional update was duplicated")
        actual[record_id] = (
            event.get("required_status"),
            event.get("requested_status"),
            event.get("before_status"),
            event.get("after_status"),
            event.get("updated"),
        )
        intervals.append(execution_interval(event, f"update event {index}"))

    expected = {
        record_id: (starting, final, starting, final, True)
        for record_id, (_name, starting, final) in TARGETS.items()
    }
    if actual != expected:
        fail("the independently guarded updates were not exactly the requested changes")
    if min(value[0] for value in intervals) <= max(
        value[1] for value in retrieval_intervals
    ):
        fail("an update started before both complete records were returned")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independently eligible updates did not execute concurrently")


def main() -> None:
    require_digest(EXECUTABLE, EXPECTED_EXECUTABLE_SHA256, "hospitality executable")
    require_digest(
        DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "hospitality documentation"
    )
    require_digest(
        INITIAL_RECORDS, EXPECTED_INITIAL_RECORDS_SHA256, "initial records"
    )
    require_digest(
        INITIAL_NOTIFICATIONS,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_digest(INITIAL_AUDIT, EXPECTED_INITIAL_AUDIT_SHA256, "initial journal")
    require_digest(AUDIT_KEY, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial = record_map(
        load_object(INITIAL_RECORDS, "protected initial records"),
        "protected initial records",
    )
    verify_state(initial)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four hospitality operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("execution-journal sequence is incomplete or reordered")
    validate_seals(events)

    retrieval_intervals = verify_retrievals(events[:2], initial)
    verify_updates(events[2:], retrieval_intervals)
    print(
        "verification passed: both concurrent retrievals preceded both concurrent, independently guarded hospitality updates; all other records and notifications are unchanged"
    )


if __name__ == "__main__":
    main()
