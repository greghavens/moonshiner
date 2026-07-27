#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0186."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".orders"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
WINTER_ID = "com-286"
FIELD_ID = "com-686"
CANCEL_REASON = "Requester chose a replacement order"


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
        try:
            label = path.relative_to(ROOT)
        except ValueError:
            label = path
        fail(f"cannot read {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.name} is not a JSON object")
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
        fail(f"{label} contains an invalid order")
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


def intervals_overlap(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def notification_digest(notification: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(notification)).hexdigest()


def expected_notification() -> dict[str, Any]:
    return {
        "body": (
            "Field notebook order was cancelled after status verification. "
            f"Reason: {CANCEL_REASON}."
        ),
        "ordinal": 1,
        "recipient": "order operations",
        "record_id": FIELD_ID,
        "subject": "Order cancellation: Field notebook order",
    }


def verify_protected_fixture(initial: dict[str, dict[str, Any]]) -> None:
    if initial.get(WINTER_ID, {}).get("name") != "Winter coat drive order":
        fail("protected Winter coat fixture has the wrong name")
    if initial.get(WINTER_ID, {}).get("location") != "Boise":
        fail("protected Winter coat fixture has the wrong location")
    if initial.get(WINTER_ID, {}).get("status") != "processing":
        fail("protected Winter coat fixture has the wrong status")
    if initial.get(FIELD_ID, {}).get("name") != "Field notebook order":
        fail("protected Field notebook fixture has the wrong name")
    if initial.get(FIELD_ID, {}).get("location") != "Phoenix":
        fail("protected Field notebook fixture has the wrong location")
    if initial.get(FIELD_ID, {}).get("status") != "backordered":
        fail("protected Field notebook fixture has the wrong status")


def verify_final_state(initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current_document = load_object(STATE_DIR / "records.json")
    current = record_map(current_document, "current orders")
    if set(current) != set(initial):
        fail("orders were created or deleted")
    for record_id, original in initial.items():
        if record_id == FIELD_ID:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = CANCEL_REASON
            if current[record_id] != expected:
                fail("Field notebook order was not cancelled exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted order {record_id} was modified")
    if current[WINTER_ID] != initial[WINTER_ID]:
        fail("Winter coat drive order was modified")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    if initial_notifications != {"notifications": [], "version": 1}:
        fail("protected notification fixture is invalid")
    current_notifications = load_object(STATE_DIR / "notifications.json")
    notifications = current_notifications.get("notifications")
    if current_notifications.get("version") != 1 or not isinstance(
        notifications, list
    ):
        fail("notification store has an invalid shape")
    expected = expected_notification()
    if notifications != [expected]:
        fail("order operations did not receive exactly the required outcome notice")
    return expected


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Winter coat drive order", "Boise"): [WINTER_ID],
        ("Field notebook order", "Phoenix"): [FIELD_ID],
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        scope = (event.get("name"), event.get("location"))
        if scope in observed:
            fail("a required search was duplicated")
        observed[scope] = event.get("result_ids")
        intervals.append(require_interval(event, f"search event {index}"))
    if observed != expected:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two searches did not execute concurrently")
    return intervals


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_hashes = {
        WINTER_ID: record_digest(initial[WINTER_ID]),
        FIELD_ID: record_digest(initial[FIELD_ID]),
    }
    observed_hashes: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete order")
        record_id = event.get("record_id")
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        expected_status = initial.get(record_id, {}).get("status")
        if event.get("status") != expected_status:
            fail(f"retrieval event {index} has an incorrect retrieved status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def main() -> None:
    initial_document = load_object(INITIAL_RECORDS_PATH)
    initial = record_map(initial_document, "protected initial orders")
    verify_protected_fixture(initial)
    expected_notice = verify_final_state(initial)

    events = load_audit()
    if len(events) != 6:
        fail(f"expected exactly six commerce operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if events[4].get("operation") != "cancel":
        fail("the fifth operation was not the conditional cancellation")
    if events[5].get("operation") != "notify":
        fail("the final operation was not the dependent notice")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a commerce operation failed")

    search_intervals = verify_searches(events[:2])
    get_intervals = verify_gets(events[2:4], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    cancellation = events[4]
    cancel_interval = require_interval(cancellation, "cancellation event")
    if cancel_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the cancellation started before both orders were retrieved")
    if (
        cancellation.get("record_id") != FIELD_ID
        or cancellation.get("before_status") != "backordered"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("reason") != CANCEL_REASON
        or cancellation.get("cancelled") is not True
    ):
        fail("the conditional cancellation was not the permitted mutation")

    notice = events[5]
    notice_interval = require_interval(notice, "notice event")
    if notice_interval[0] <= cancel_interval[1]:
        fail("the notice started before the cancellation succeeded")
    if (
        notice.get("record_id") != FIELD_ID
        or notice.get("recipient") != "order operations"
        or notice.get("notification_sha256")
        != notification_digest(expected_notice)
    ):
        fail("the dependent notification was not exactly the required outcome notice")

    print(
        "verification passed: parallel searches and retrievals preceded the single "
        "Field notebook cancellation and its dependent order-operations notice"
    )


if __name__ == "__main__":
    main()
