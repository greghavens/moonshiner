#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0160."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".fleet"
INITIAL_RECORDS = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS = ROOT / ".protected" / "initial_notifications.json"
INITIAL_AUDIT = ROOT / ".protected" / "initial_audit.jsonl"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
EXECUTABLE = ROOT / "fleetctl"

EXPECTED_EXECUTABLE_SHA256 = "d812c96a0f944405645d53c0cda70d11349da1d74eb53b5be748bc6d354a1da2"
EXPECTED_INITIAL_RECORDS_SHA256 = "fc36328d8c467209c8c938aedf561d700624985a6736081cec121a6b1004fe67"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_INITIAL_AUDIT_SHA256 = "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b"
EXPECTED_AUDIT_KEY_SHA256 = "2aebde29b34cfe075b3749a88b7ac393735d6f56d0ab2373c54c6760886fb923"

HATCHBACK_ID = "fle-260"
TRUCK_ID = "fle-660"
TARGETS = {
    HATCHBACK_ID: ("Electric hatchback 9", "available", "reserved"),
    TRUCK_ID: ("Maintenance truck 6", "maintenance-due", "maintenance-scheduled"),
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


def record_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
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
                    fail(f"execution-journal line {line_number} is invalid JSON: {error}")
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


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
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


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    actual: dict[Any, tuple[Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        record_id = event.get("record_id")
        if record_id in actual:
            fail("a required complete-record retrieval was duplicated")
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        actual[record_id] = (event.get("record_sha256"), event.get("status"))
        intervals.append(interval(event, f"retrieval event {index}"))

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
    events: list[dict[str, Any]], get_intervals: list[tuple[int, int]]
) -> None:
    actual: dict[Any, tuple[Any, Any, Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:], 3):
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
        intervals.append(interval(event, f"update event {index}"))

    expected = {
        record_id: (starting, final, starting, final, True)
        for record_id, (_name, starting, final) in TARGETS.items()
    }
    if actual != expected:
        fail("the independently guarded updates were not exactly the requested changes")
    if min(value[0] for value in intervals) <= max(
        value[1] for value in get_intervals
    ):
        fail("an update started before both complete records were returned")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independently eligible updates did not execute concurrently")


def main() -> None:
    require_digest(EXECUTABLE, EXPECTED_EXECUTABLE_SHA256, "fleet executable")
    require_digest(INITIAL_RECORDS, EXPECTED_INITIAL_RECORDS_SHA256, "initial records")
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
        fail(f"expected exactly four fleet operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("execution-journal sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["get", "get"]:
        fail("the first operation layer was not exactly two record retrievals")
    if [event.get("operation") for event in events[2:]] != ["update", "update"]:
        fail("the final operation layer was not exactly two conditional updates")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a fleet operation failed or its condition did not match")

    get_intervals = verify_gets(events, initial)
    verify_updates(events, get_intervals)
    print(
        "verification passed: both concurrent retrievals preceded both concurrent, independently guarded fleet updates; all other records and notifications are unchanged"
    )


if __name__ == "__main__":
    main()
