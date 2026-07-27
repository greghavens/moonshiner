#!/usr/bin/env python3
"""Protected deterministic verification for the calendar reconciliation."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".calendar"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
MENTOR_ID = "cal-261"
FACILITIES_ID = "cal-661"
EXPECTED_FILES = {
    ".calendar/audit.jsonl",
    ".calendar/notifications.json",
    ".calendar/records.json",
    ".calendar/service.lock",
    ".gitignore",
    ".protected/audit.key",
    ".protected/initial_notifications.json",
    ".protected/initial_records.json",
    ".protected/verify.py",
    "calendarctl",
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
    mapped = {record["id"]: record for record in records}
    if len(mapped) != len(records):
        fail(f"{label} contains duplicate IDs")
    return mapped


def load_events() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with (STATE_DIR / "audit.jsonl").open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                event = json.loads(line)
                if not isinstance(event, dict):
                    fail(f"audit event {line_number} is not an object")
                events.append(event)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"audit event {index} has no integrity seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"audit event {index} was not emitted intact by calendarctl")


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


def parent_fingerprint(event: dict[str, Any]) -> tuple[Any, Any]:
    return event.get("parent_pid"), event.get("parent_start_ticks")


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_files() -> None:
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    }
    missing = sorted(EXPECTED_FILES - actual)
    extras = sorted(actual - EXPECTED_FILES)
    if missing:
        fail("required sandbox files are missing: " + ", ".join(missing))
    if extras:
        fail("unexpected workspace files remain: " + ", ".join(extras))


def verify_final_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if set(current) != set(initial):
        fail("calendar records were created or deleted")
    expected_statuses = {MENTOR_ID: "completed", FACILITIES_ID: "confirmed"}
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in expected_statuses:
            expected["status"] = expected_statuses[record_id]
        if current.get(record_id) != expected:
            fail(f"record {record_id} does not have its exact required final state")
    if load_object(STATE_DIR / "notifications.json") != load_object(
        INITIAL_NOTIFICATIONS_PATH
    ):
        fail("notification state changed")


def verify_gets(
    gets: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_ids = {MENTOR_ID, FACILITIES_ID}
    if {event.get("record_id") for event in gets} != expected_ids:
        fail("the get layer did not retrieve exactly the two supplied IDs")
    if len({event.get("pid") for event in gets}) != 2:
        fail("the two gets were not distinct executable processes")
    if len({parent_fingerprint(event) for event in gets}) != 1:
        fail("the two gets were not launched together in one shell execution call")
    intervals = [interval(event, f"get event {index}") for index, event in enumerate(gets, 1)]
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two get processes did not execute concurrently")
    for event in gets:
        record_id = event.get("record_id")
        record = initial.get(record_id)
        if (
            record is None
            or event.get("found") is not True
            or event.get("outcome") != "ok"
            or event.get("record_sha256") != record_digest(record)
            or event.get("status") != record.get("status")
        ):
            fail(f"full-record retrieval evidence is invalid for {record_id}")
    return intervals


def verify_updates(
    updates: list[dict[str, Any]], get_by_id: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected = {
        MENTOR_ID: ("confirmed", "completed"),
        FACILITIES_ID: ("tentative", "confirmed"),
    }
    if {event.get("record_id") for event in updates} != set(expected):
        fail("the update layer did not target exactly the two eligible records")
    if len({event.get("pid") for event in updates}) != 2:
        fail("the two updates were not distinct executable processes")
    if len({parent_fingerprint(event) for event in updates}) != 1:
        fail("the two updates were not launched together in one later shell call")
    intervals = [
        interval(event, f"update event {index}")
        for index, event in enumerate(updates, 3)
    ]
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the independent update processes did not execute concurrently")
    for event in updates:
        record_id = event.get("record_id")
        before, after = expected[record_id]
        retrieved = get_by_id.get(record_id, {})
        if retrieved.get("status") != before:
            fail(f"the update condition was not established by the get for {record_id}")
        if (
            event.get("expected_status") != before
            or event.get("before_status") != before
            or event.get("requested_status") != after
            or event.get("after_status") != after
            or event.get("precondition_matched") is not True
            or event.get("changed") is not True
            or event.get("outcome") != "ok"
        ):
            fail(f"conditional transition evidence is invalid for {record_id}")
    return intervals


def main() -> None:
    verify_files()
    initial = record_map(load_object(INITIAL_RECORDS_PATH), "protected initial records")
    if initial.get(MENTOR_ID, {}).get("status") != "confirmed":
        fail("protected Mentor office hours fixture is invalid")
    if initial.get(FACILITIES_ID, {}).get("status") != "tentative":
        fail("protected Facilities handoff fixture is invalid")
    verify_final_state(initial)

    events = load_events()
    if len(events) != 4:
        fail(f"expected exactly four calendar operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("operation history is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["get", "get"]:
        fail("the first dependency layer was not exactly two gets")
    if [event.get("operation") for event in events[2:]] != ["update", "update"]:
        fail("the second dependency layer was not exactly two updates")

    gets = events[:2]
    updates = events[2:]
    get_intervals = verify_gets(gets, initial)
    update_intervals = verify_updates(
        updates, {event["record_id"]: event for event in gets}
    )
    if min(value[0] for value in update_intervals) <= max(
        value[1] for value in get_intervals
    ):
        fail("an update started before both current records were returned")
    if parent_fingerprint(gets[0]) == parent_fingerprint(updates[0]):
        fail("the read and update layers were collapsed into one shell execution call")

    print(
        "verification passed: both current records were retrieved together before "
        "the two independent conditional updates ran together"
    )


if __name__ == "__main__":
    main()
