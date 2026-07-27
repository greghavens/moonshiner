#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0169."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".crm"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "crmctl"
EXPECTED_EXECUTABLE_SHA256 = "7650b75fd3108047b26226d9c104a2d408b869efcc5d48c19911af4528d47d74"
EXPECTED_INITIAL_RECORDS_SHA256 = "c83aa0acd9797aae6de0db1f8185ed07a4a90dc5d4132d66220ea0512b4fc66e"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_AUDIT_KEY_SHA256 = "bbd70d51d5a1a45ae6b2e9aef5baccf6f4529aae7f5faa85cfb30c86dce4f156"
REDSTONE_ID = "crm-269"
WILLOW_ID = "crm-669"


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
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains a duplicate ID")
    return result


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
        fail(f"cannot read CRM operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"operation event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"operation event {index} has an invalid seal")


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
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "CRM executable"
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
    if initial.get(REDSTONE_ID, {}).get("status") != "active":
        fail("protected Redstone fixture has the wrong starting status")
    if initial.get(WILLOW_ID, {}).get("status") != "prospect":
        fail("protected Willow fixture has the wrong starting status")

    expected_statuses = {
        REDSTONE_ID: "review-required",
        WILLOW_ID: "active",
    }
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in expected_statuses:
            expected["status"] = expected_statuses[record_id]
        if current[record_id] != expected:
            if record_id in expected_statuses:
                fail(f"target record {record_id} was not changed exactly as required")
            fail(f"untargeted record {record_id} was modified")

    initial_notifications = load_json(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_json(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("a notification was created or notification state changed")

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four CRM operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("CRM operation sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["get", "get"]:
        fail("the first dependency layer was not exactly two retrievals")
    if [event.get("operation") for event in events[2:]] != ["update", "update"]:
        fail("the second dependency layer was not exactly two updates")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a CRM operation failed")

    expected_gets = {
        REDSTONE_ID: {
            "name": "Redstone Family Clinic",
            "status": "active",
            "record_sha256": record_digest(initial[REDSTONE_ID]),
        },
        WILLOW_ID: {
            "name": "Willow Creek Nursery",
            "status": "prospect",
            "record_sha256": record_digest(initial[WILLOW_ID]),
        },
    }
    actual_gets: dict[Any, dict[str, Any]] = {}
    get_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = event.get("record_id")
        if record_id in actual_gets:
            fail("a required retrieval was duplicated")
        actual_gets[record_id] = {
            "name": event.get("name"),
            "status": event.get("status"),
            "record_sha256": event.get("record_sha256"),
        }
        get_intervals.append(require_interval(event, f"retrieval event {index}"))
    if actual_gets != expected_gets:
        fail("retrievals did not return both required current records")
    if not intervals_overlap(get_intervals[0], get_intervals[1]):
        fail("the two retrievals did not execute concurrently")

    expected_updates = {
        REDSTONE_ID: {
            "before_status": "active",
            "after_status": "review-required",
            "updated": 1,
        },
        WILLOW_ID: {
            "before_status": "prospect",
            "after_status": "active",
            "updated": 1,
        },
    }
    actual_updates: dict[Any, dict[str, Any]] = {}
    update_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:], 3):
        record_id = event.get("record_id")
        if record_id in actual_updates:
            fail("a conditional update was duplicated")
        actual_updates[record_id] = {
            "before_status": event.get("before_status"),
            "after_status": event.get("after_status"),
            "updated": event.get("updated"),
        }
        update_intervals.append(require_interval(event, f"update event {index}"))
    if actual_updates != expected_updates:
        fail("the conditional updates were not the two permitted status changes")
    if not intervals_overlap(update_intervals[0], update_intervals[1]):
        fail("the two eligible updates did not execute concurrently")
    if min(interval[0] for interval in update_intervals) < max(
        interval[1] for interval in get_intervals
    ):
        fail("an update started before both current records were returned")

    print(
        "verification passed: concurrent current-record retrievals preceded only the two eligible concurrent CRM updates"
    )


if __name__ == "__main__":
    main()
