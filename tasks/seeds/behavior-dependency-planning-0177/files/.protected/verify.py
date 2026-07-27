#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0177."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".facilities"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "facilitiesctl"
EXPECTED_EXECUTABLE_SHA256 = "2a377590a6a4f98495268d1d754dd80c18dd4a283779986e32d995776edb1375"
EXPECTED_INITIAL_RECORDS_SHA256 = "a01d6802e33880b9d576d3a678096645ff7b28f595ee707d4c35a9e46101f859"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "d4d2ad01004144b277287ec1736fc5fed9d33cc6e1b7e12dd48b4997490d5704"
EXPECTED_AUDIT_KEY_SHA256 = "6ae2a3e4c8be91982504fdada353e4b0cb2bf2d985b821a5ac80ab3a332656f8"
KITCHEN_ID = "fac-277"
WELLNESS_ID = "fac-677"
REQUIRED_REASON = "Request superseded by a consolidated work order"
REQUIRED_RECIPIENT = "facilities dispatch"


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
        fail(f"cannot read operation history: {error}")
    return events


def require_protected_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


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


def verify_final_state(initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current = record_map(
        load_object(STATE_DIR / "records.json"), "current facilities records"
    )
    if set(current) != set(initial):
        fail("facilities requests were created or deleted")
    if initial.get(WELLNESS_ID, {}).get("status") != "queued":
        fail("protected Wellness room paint fixture is invalid")

    expected_wellness = dict(initial[WELLNESS_ID])
    expected_wellness["status"] = "cancelled"
    expected_wellness["cancellation_reason"] = REQUIRED_REASON
    for record_id, original in initial.items():
        expected = expected_wellness if record_id == WELLNESS_ID else original
        if current[record_id] != expected:
            fail(f"request {record_id} differs from its permitted final state")
    if current[KITCHEN_ID] != initial[KITCHEN_ID]:
        fail("Kitchen exhaust repair was modified")
    return current[WELLNESS_ID]


def message_describes_outcome(message: Any) -> bool:
    if not isinstance(message, str) or not message.strip():
        return False
    normalized = message.casefold()
    required_fragments = (
        "wellness room paint request",
        WELLNESS_ID.casefold(),
        "cancelled",
        REQUIRED_REASON.casefold(),
    )
    return all(fragment in normalized for fragment in required_fragments)


def verify_notifications() -> dict[str, Any]:
    initial_document = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_document = load_object(STATE_DIR / "notifications.json")
    initial = initial_document.get("notifications")
    current = current_document.get("notifications")
    if (
        initial_document.get("version") != 1
        or current_document.get("version") != 1
        or not isinstance(initial, list)
        or not isinstance(current, list)
    ):
        fail("notification store has an invalid shape")
    if current[:-1] != initial or len(current) != len(initial) + 1:
        fail("notification history was changed beyond one appended notice")
    notification = current[-1]
    if not isinstance(notification, dict) or set(notification) != {
        "ordinal",
        "recipient",
        "record_id",
        "message",
    }:
        fail("the appended notification has an invalid shape")
    if (
        notification.get("ordinal") != len(initial) + 1
        or notification.get("recipient") != REQUIRED_RECIPIENT
        or notification.get("record_id") != WELLNESS_ID
        or not message_describes_outcome(notification.get("message"))
    ):
        fail("dispatch was not sent the required cancellation outcome")
    return notification


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Kitchen exhaust repair", "Building A"): [KITCHEN_ID],
        ("Wellness room paint request", "Building B"): [WELLNESS_ID],
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
        KITCHEN_ID: record_digest(initial[KITCHEN_ID]),
        WELLNESS_ID: record_digest(initial[WELLNESS_ID]),
    }
    observed_hashes: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete request")
        record_id = event.get("record_id")
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        if event.get("status") != initial.get(record_id, {}).get("status"):
            fail(f"retrieval event {index} did not preserve the retrieved status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two complete-request retrievals did not execute concurrently")
    return intervals


def main() -> None:
    require_protected_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "facilities executable"
    )
    require_protected_digest(
        INITIAL_RECORDS_PATH,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial facilities records",
    )
    require_protected_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_protected_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial facilities records"
    )
    final_wellness = verify_final_state(initial)
    notification = verify_notifications()

    events = load_audit()
    if len(events) != 6:
        fail(f"expected exactly six facilities operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6]:
        fail("operation sequence is incomplete or reordered")
    validate_seals(events)
    expected_operations = ["search", "search", "get", "get", "cancel", "notify"]
    if [event.get("operation") for event in events] != expected_operations:
        fail("facilities operations did not follow the required dependency layers")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a recorded facilities operation failed")

    search_intervals = verify_searches(events[:2])
    get_intervals = verify_gets(events[2:4], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    cancellation = events[4]
    cancel_interval = require_interval(cancellation, "cancellation event")
    if cancel_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("cancellation started before both complete requests were retrieved")
    if (
        cancellation.get("record_id") != WELLNESS_ID
        or cancellation.get("before_status") != "queued"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("reason") != REQUIRED_REASON
        or cancellation.get("cancelled") is not True
        or cancellation.get("record_sha256") != record_digest(final_wellness)
    ):
        fail("the conditional cancellation was not exactly the permitted change")

    notice = events[5]
    notice_interval = require_interval(notice, "notification event")
    if notice_interval[0] <= cancel_interval[1]:
        fail("notification started before cancellation succeeded")
    if (
        notice.get("record_id") != WELLNESS_ID
        or notice.get("recipient") != REQUIRED_RECIPIENT
        or notice.get("delivered") is not True
        or notice.get("notification_sha256")
        != hashlib.sha256(canonical(notification)).hexdigest()
    ):
        fail("the sole dispatch notification does not match the successful outcome")

    print(
        "verification passed: parallel search and retrieval layers preceded only "
        "the Wellness cancellation and its dependent dispatch notice"
    )


if __name__ == "__main__":
    main()
