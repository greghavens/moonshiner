#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0152."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".education"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"
KEY_PATH = ROOT / ".harness" / "audit.key"
EXECUTABLE_PATH = ROOT / "education-records"
EXPECTED_EXECUTABLE_SHA256 = "ac08bad185d40c8965e6c115d81e29b983956949666ac2ba185e5deeb1b01941"
EXPECTED_INITIAL_RECORDS_SHA256 = "4402af804790239fc17d8327f58caddd998c31a1ad75ccc74aad7f48542273d1"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_AUDIT_KEY_SHA256 = "6d7a240abb5aa12b3f5012191e2576b6176a8053f45214963725d46ee55a9da8"
ACCOUNTING_ID = "edu-252"
ILLUSTRATION_ID = "edu-652"


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")
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
        fail(f"{label} contains a duplicate ID")
    return mapped


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with (STATE_DIR / "audit.jsonl").open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"audit line {line_number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"audit line {line_number} is not an object")
                events.append(event)
    except OSError as error:
        fail(f"cannot read audit log: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"audit event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"audit event {index} has an invalid seal")


def require_interval(event: dict[str, Any], label: str) -> tuple[int, int]:
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


def intervals_overlap(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def require_protected_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def main() -> None:
    require_protected_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "education executable"
    )
    require_protected_digest(
        INITIAL_RECORDS_PATH,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial records",
    )
    require_protected_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_protected_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial_document = load_json(INITIAL_RECORDS_PATH)
    current_document = load_json(STATE_DIR / "records.json")
    initial = record_map(initial_document, "protected initial records")
    current = record_map(current_document, "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")

    if (
        initial.get(ACCOUNTING_ID, {}).get("title") != "Evening Accounting"
        or initial.get(ACCOUNTING_ID, {}).get("status") != "open"
        or initial.get(ILLUSTRATION_ID, {}).get("title")
        != "Digital Illustration"
        or initial.get(ILLUSTRATION_ID, {}).get("status") != "waitlisted"
    ):
        fail("protected target fixtures are invalid")

    required_statuses = {
        ACCOUNTING_ID: "enrollment-ready",
        ILLUSTRATION_ID: "open",
    }
    for record_id, original in initial.items():
        if record_id in required_statuses:
            expected = dict(original)
            expected["status"] = required_statuses[record_id]
            if current[record_id] != expected:
                fail(f"target record {record_id} was not changed exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted record {record_id} was modified")

    initial_notifications = load_json(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_json(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("a notification was created or notification state changed")

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four education-record operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["get", "get"]:
        fail("the first operation layer was not exactly two retrievals")
    if [event.get("operation") for event in events[2:]] != [
        "update",
        "update",
    ]:
        fail("the final operation layer was not exactly two updates")
    if any(event.get("outcome") != "ok" for event in events):
        fail("an education-record operation failed or a condition did not match")

    expected_gets = {
        ACCOUNTING_ID: (record_digest(initial[ACCOUNTING_ID]), "open"),
        ILLUSTRATION_ID: (
            record_digest(initial[ILLUSTRATION_ID]),
            "waitlisted",
        ),
    }
    actual_gets: dict[Any, tuple[Any, Any]] = {}
    get_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = event.get("record_id")
        if record_id in actual_gets:
            fail("a required retrieval was duplicated")
        actual_gets[record_id] = (
            event.get("record_sha256"),
            event.get("status"),
        )
        get_intervals.append(require_interval(event, f"retrieval event {index}"))
    if actual_gets != expected_gets:
        fail("retrievals were incorrect, incomplete, or not of the original records")
    if not intervals_overlap(get_intervals[0], get_intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")

    expected_updates = {
        ACCOUNTING_ID: (
            "open",
            "enrollment-ready",
            "open",
            "enrollment-ready",
        ),
        ILLUSTRATION_ID: ("waitlisted", "open", "waitlisted", "open"),
    }
    actual_updates: dict[Any, tuple[Any, Any, Any, Any]] = {}
    update_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:], 3):
        record_id = event.get("record_id")
        if record_id in actual_updates:
            fail("a required update was duplicated")
        if event.get("updated") != 1:
            fail(f"update event {index} did not change exactly one record")
        actual_updates[record_id] = (
            event.get("required_status"),
            event.get("requested_status"),
            event.get("before_status"),
            event.get("after_status"),
        )
        update_intervals.append(require_interval(event, f"update event {index}"))
    if actual_updates != expected_updates:
        fail("the independently guarded updates were not exactly the required changes")
    if min(interval[0] for interval in update_intervals) <= max(
        interval[1] for interval in get_intervals
    ):
        fail("an update started before both complete records were returned")
    if not intervals_overlap(update_intervals[0], update_intervals[1]):
        fail("the two independently eligible updates were not issued together")

    print(
        "verification passed: both concurrent retrievals preceded the two concurrent, independently guarded education updates"
    )


if __name__ == "__main__":
    main()
