#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0162."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".travel"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"
KEY_PATH = ROOT / ".harness" / "audit.key"
LISBON_ID = "tra-262"
SEATTLE_ID = "tra-662"
TARGETS = {
    LISBON_ID: ("Lisbon museum visit", "approved", "on-hold"),
    SEATTLE_ID: ("Seattle planning retreat", "draft", "approved"),
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_object(path: Path) -> dict[str, Any]:
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


def intervals_overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_fixture(initial: dict[str, dict[str, Any]]) -> None:
    for record_id, (name, old_status, _new_status) in TARGETS.items():
        record = initial.get(record_id)
        if record is None:
            fail(f"protected fixture is missing {record_id}")
        if record.get("name") != name or record.get("status") != old_status:
            fail(f"protected fixture for {record_id} is invalid")


def verify_final_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    for record_id, original in initial.items():
        if record_id in TARGETS:
            expected = dict(original)
            expected["status"] = TARGETS[record_id][2]
            if current[record_id] != expected:
                fail(f"{record_id} was not changed exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted record {record_id} was modified")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")


def verify_retrievals(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_hashes = {
        record_id: record_digest(initial[record_id]) for record_id in TARGETS
    }
    observed_hashes: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        if event.get("operation") != "get" or event.get("outcome") != "ok":
            fail("the first dependency layer was not exactly two successful retrievals")
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = event.get("record_id")
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        if record_id not in TARGETS:
            fail("an unrelated record was retrieved")
        if event.get("status") != TARGETS[record_id][1]:
            fail(f"retrieval event {index} has the wrong current status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("the two complete target records were not both returned")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def verify_updates(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    observed: dict[Any, tuple[Any, Any, Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("operation") != "update" or event.get("outcome") != "ok":
            fail("the second dependency layer was not exactly two successful updates")
        record_id = event.get("record_id")
        if record_id in observed:
            fail("a target update was duplicated")
        observed[record_id] = (
            event.get("before_status"),
            event.get("expected_status"),
            event.get("requested_status"),
            event.get("after_status"),
            event.get("updated"),
        )
        intervals.append(require_interval(event, f"update event {index}"))
    expected = {
        record_id: (old_status, old_status, new_status, new_status, True)
        for record_id, (_name, old_status, new_status) in TARGETS.items()
    }
    if observed != expected:
        fail("conditional updates did not exactly match both permitted transitions")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independent updates were not issued concurrently")
    return intervals


def main() -> None:
    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial records"
    )
    verify_fixture(initial)
    verify_final_state(initial)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four Travel Desk operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)

    retrieval_intervals = verify_retrievals(events[:2], initial)
    update_intervals = verify_updates(events[2:])
    if min(interval[0] for interval in update_intervals) <= max(
        interval[1] for interval in retrieval_intervals
    ):
        fail("an update started before both current records were returned")

    print(
        "verification passed: parallel current-record retrievals preceded both parallel guarded status updates"
    )


if __name__ == "__main__":
    main()
