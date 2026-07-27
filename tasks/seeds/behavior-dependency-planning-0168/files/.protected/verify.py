#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0168."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".shipping"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
PHOTO_ID = "shi-268"
PARTS_ID = "shi-668"
EXPECTED_FILES = {
    ".gitignore",
    ".protected/audit.key",
    ".protected/initial_notifications.json",
    ".protected/initial_records.json",
    ".protected/verify.py",
    ".shipping/audit.jsonl",
    ".shipping/lock",
    ".shipping/notifications.json",
    ".shipping/records.json",
    "shippingctl",
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
    if not key:
        fail("protected audit key is empty")
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


def verify_workspace_files() -> None:
    actual_files = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.relative_to(ROOT).parts
        and "__pycache__" not in path.relative_to(ROOT).parts
    }
    extras = sorted(actual_files - EXPECTED_FILES)
    missing = sorted(EXPECTED_FILES - actual_files)
    if extras:
        fail("unexpected workspace files remain: " + ", ".join(extras))
    if missing:
        fail("required sandbox files are missing: " + ", ".join(missing))


def verify_final_state(initial: dict[str, dict[str, Any]]) -> None:
    current_document = load_object(STATE_DIR / "records.json")
    current = record_map(current_document, "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    if initial.get(PHOTO_ID, {}).get("name") != "Archive photo package":
        fail("protected Archive photo package fixture is invalid")
    if initial.get(PHOTO_ID, {}).get("status") != "in-transit":
        fail("protected Archive photo package starting status is invalid")
    if initial.get(PARTS_ID, {}).get("name") != "Replacement parts parcel":
        fail("protected Replacement parts parcel fixture is invalid")
    if initial.get(PARTS_ID, {}).get("status") != "label-created":
        fail("protected Replacement parts parcel starting status is invalid")

    expected_transitions = {PHOTO_ID: "delivered", PARTS_ID: "in-transit"}
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in expected_transitions:
            expected["status"] = expected_transitions[record_id]
        if current[record_id] != expected:
            fail(f"record {record_id} is not in its exact permitted final state")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_ids = {PHOTO_ID, PARTS_ID}
    observed_ids: set[Any] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        record_id = event.get("record_id")
        if record_id in observed_ids:
            fail("a required retrieval was duplicated")
        observed_ids.add(record_id)
        original = initial.get(record_id)
        if original is None:
            fail(f"retrieval event {index} targeted an out-of-scope record")
        if (
            event.get("found") is not True
            or event.get("outcome") != "ok"
            or event.get("name") != original.get("name")
            or event.get("status") != original.get("status")
            or event.get("record_sha256") != record_digest(original)
        ):
            fail(f"retrieval event {index} is not intact full-record evidence")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_ids != expected_ids:
        fail("the two exact requested records were not both retrieved")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independent retrievals did not execute concurrently")
    return intervals


def verify_updates(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        PHOTO_ID: {
            "before_status": "in-transit",
            "conditional_status": "in-transit",
            "after_status": "delivered",
        },
        PARTS_ID: {
            "before_status": "label-created",
            "conditional_status": "label-created",
            "after_status": "in-transit",
        },
    }
    observed_ids: set[Any] = set()
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        record_id = event.get("record_id")
        if record_id in observed_ids:
            fail("a conditional update was duplicated")
        observed_ids.add(record_id)
        transition = expected.get(record_id)
        if transition is None:
            fail(f"update event {index} targeted an out-of-scope record")
        if (
            event.get("outcome") != "ok"
            or event.get("updated") is not True
            or any(event.get(field) != value for field, value in transition.items())
        ):
            fail(f"update event {index} is not the permitted conditional transition")
        intervals.append(require_interval(event, f"update event {index}"))
    if observed_ids != {PHOTO_ID, PARTS_ID}:
        fail("both eligible records were not conditionally updated")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independent conditional updates were not issued together")
    return intervals


def main() -> None:
    verify_workspace_files()
    initial_document = load_object(INITIAL_RECORDS_PATH)
    initial = record_map(initial_document, "protected initial records")
    verify_final_state(initial)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four shipping operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["get", "get"]:
        fail("the first dependency layer was not exactly two retrievals")
    if [event.get("operation") for event in events[2:]] != ["update", "update"]:
        fail("the second dependency layer was not exactly two updates")

    get_intervals = verify_gets(events[:2], initial)
    update_intervals = verify_updates(events[2:])
    if min(interval[0] for interval in update_intervals) <= max(
        interval[1] for interval in get_intervals
    ):
        fail("an update started before both current records were returned")

    print(
        "verification passed: concurrent retrievals preceded both independent conditional shipment updates"
    )


if __name__ == "__main__":
    main()
