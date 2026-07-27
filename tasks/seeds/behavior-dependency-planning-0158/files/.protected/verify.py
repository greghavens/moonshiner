#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0158."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".telecom"
INITIAL_RECORDS = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS = ROOT / ".protected" / "initial_notifications.json"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
EXECUTABLE = ROOT / "telecomctl"
REPORT = ROOT / "telecom-review.md"

EXPECTED_EXECUTABLE_SHA256 = "6b9036feeb9c3840c787ad4c31a0160044209a12373bbe3aad3f71dba2a2b602"
EXPECTED_INITIAL_RECORDS_SHA256 = "34816317955640fe3ddcbbbfd7143d5b7f5c4809b493ced08b7f3123f5e22558"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "e8f51b24d413610d10e202f654f4cdaa0feff90d9289a12d22c474f2c3c0f2a3"
EXPECTED_AUDIT_KEY_SHA256 = "497abeb45cab0fa38d64159442403f13c5cf4d8d91739c156818480d2d233cc8"

CLINIC_ID = "tel-258"
MUSEUM_ID = "tel-658"
TARGETS = {
    CLINIC_ID: ("active", "review-required"),
    MUSEUM_ID: ("pending-activation", "active"),
}
EXPECTED_REPORT = (
    "- tel-258 | Clinic backup line | Community Center | "
    "retrieved status active | final status review-required\n"
    "- tel-658 | Museum kiosk plan | Family Account | "
    "retrieved status pending-activation | final status active\n"
)


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
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"audit line {line_number} is invalid JSON: {error}")
                if not isinstance(value, dict):
                    fail(f"audit line {line_number} is not an object")
                events.append(value)
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
            fail(f"audit event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"audit event {index} has an invalid seal")


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
        load_object(STATE / "records.json", "current records"),
        "current records",
    )
    if set(current) != set(initial):
        fail("records were created or deleted")
    for record_id, original in initial.items():
        expected = dict(original)
        if record_id in TARGETS:
            starting, final = TARGETS[record_id]
            if original.get("status") != starting:
                fail(f"protected starting status for {record_id} is invalid")
            expected["status"] = final
        if current[record_id] != expected:
            if record_id in TARGETS:
                fail(f"{record_id} was not changed exactly as required")
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
    events: list[dict[str, Any]],
    initial: dict[str, dict[str, Any]],
    help_interval: tuple[int, int],
) -> list[tuple[int, int]]:
    gets = events[1:3]
    actual: dict[Any, tuple[Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(gets, 1):
        record_id = event.get("record_id")
        if record_id in actual:
            fail("a required record retrieval was duplicated")
        actual[record_id] = (event.get("status"), event.get("record_sha256"))
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        intervals.append(interval(event, f"retrieval event {index}"))

    expected = {
        record_id: (starting, record_digest(initial[record_id]))
        for record_id, (starting, _final) in TARGETS.items()
    }
    if actual != expected:
        fail("retrievals did not return both exact initial target records")
    if min(value[0] for value in intervals) < help_interval[1]:
        fail("a current-record retrieval started before top-level help finished")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two current-record retrievals did not execute concurrently")
    return intervals


def verify_updates(
    events: list[dict[str, Any]], get_intervals: list[tuple[int, int]]
) -> None:
    updates = events[3:]
    actual: dict[Any, tuple[Any, Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(updates, 4):
        record_id = event.get("record_id")
        if record_id in actual:
            fail("a required status update was duplicated")
        actual[record_id] = (
            event.get("required_status"),
            event.get("before_status"),
            event.get("after_status"),
            event.get("updated"),
        )
        intervals.append(interval(event, f"update event {index}"))

    expected = {
        record_id: (starting, starting, final, True)
        for record_id, (starting, final) in TARGETS.items()
    }
    if actual != expected:
        fail("the two status-guarded updates were not exactly the requested changes")
    if min(value[0] for value in intervals) < max(value[1] for value in get_intervals):
        fail("an update started before both current records were returned")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two independently verified updates did not execute concurrently")


def verify_report() -> None:
    try:
        actual = REPORT.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read telecom-review.md: {error}")
    if actual != EXPECTED_REPORT:
        fail("telecom-review.md is not the grounded two-record report")


def main() -> None:
    require_digest(EXECUTABLE, EXPECTED_EXECUTABLE_SHA256, "telecom executable")
    require_digest(
        INITIAL_RECORDS,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial records",
    )
    require_digest(
        INITIAL_NOTIFICATIONS,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_digest(AUDIT_KEY, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial = record_map(
        load_object(INITIAL_RECORDS, "protected initial records"),
        "protected initial records",
    )
    verify_state(initial)

    events = load_audit()
    if len(events) != 5:
        fail(
            "expected top-level help and exactly four telecom operations, "
            f"found {len(events)} events"
        )
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5]:
        fail("execution-journal sequence is incomplete or reordered")
    validate_seals(events)
    if (
        events[0].get("operation") != "help"
        or events[0].get("scope") != "top-level"
    ):
        fail("the first executable action was not top-level help")
    if [event.get("operation") for event in events[1:3]] != ["get", "get"]:
        fail("the first operation layer was not exactly two record retrievals")
    if [event.get("operation") for event in events[3:]] != ["update", "update"]:
        fail("the second operation layer was not exactly two status updates")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a telecom operation failed")

    help_interval = interval(events[0], "top-level help event")
    get_intervals = verify_gets(events, initial, help_interval)
    verify_updates(events, get_intervals)
    verify_report()
    print(
        "verification passed: help preceded two parallel gets and two parallel guarded updates"
    )


if __name__ == "__main__":
    main()
