#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0181."""

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
LAUNCH_ID = "cal-281"
BENEFITS_ID = "cal-681"
REASON = "Organizer withdrew the tentative meeting"


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


def notification_digest(notification: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(notification)).hexdigest()


def verify_final_state(
    initial: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    if initial.get(BENEFITS_ID, {}).get("status") != "tentative":
        fail("protected Benefits renewal fixture is invalid")
    if initial.get(LAUNCH_ID, {}).get("status") != "confirmed":
        fail("protected Launch readiness fixture is invalid")
    for record_id, original in initial.items():
        if record_id == BENEFITS_ID:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = REASON
            if current[record_id] != expected:
                fail("Benefits renewal review was not cancelled exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted record {record_id} was modified")
    if current[LAUNCH_ID] != initial[LAUNCH_ID]:
        fail("Launch readiness briefing was modified")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if initial_notifications.get("notifications") != []:
        fail("protected notification fixture is invalid")
    notifications = current_notifications.get("notifications")
    if current_notifications.get("version") != 1 or not isinstance(notifications, list):
        fail("current notification store has an invalid shape")
    if len(notifications) != 1 or not isinstance(notifications[0], dict):
        fail("expected exactly one notification")
    notification = notifications[0]
    if (
        notification.get("recipient") != "scheduling desk"
        or notification.get("record_id") != BENEFITS_ID
        or notification.get("ordinal") != 1
    ):
        fail("the single notification had the wrong recipient or record")
    message = notification.get("message")
    if not isinstance(message, str):
        fail("the notification message is missing")
    normalized = " ".join(message.casefold().split())
    cancellation_word_present = (
        "cancelled" in normalized or "canceled" in normalized
    )
    if "benefits renewal review" not in normalized or not cancellation_word_present:
        fail("the notification did not state the successful cancellation outcome")
    return notification


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Benefits renewal review", "Chicago Office"): [BENEFITS_ID],
        ("Launch readiness briefing", "Denver HQ"): [LAUNCH_ID],
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
        LAUNCH_ID: record_digest(initial[LAUNCH_ID]),
        BENEFITS_ID: record_digest(initial[BENEFITS_ID]),
    }
    observed_hashes: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete record")
        record_id = event.get("record_id")
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        if event.get("status") != initial.get(record_id, {}).get("status"):
            fail(f"retrieval event {index} has the wrong retrieved status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def main() -> None:
    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial records"
    )
    notification = verify_final_state(initial)

    events = load_audit()
    if len(events) != 6:
        fail(f"expected exactly six calendar operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if [event.get("operation") for event in events[4:]] != ["cancel", "notify"]:
        fail("the dependent cancellation and notification were missing or reordered")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a calendar operation failed or was skipped")

    search_intervals = verify_searches(events[:2])
    get_intervals = verify_gets(events[2:4], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    cancel = events[4]
    cancel_interval = require_interval(cancel, "cancel event")
    if cancel_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the cancellation started before both records were retrieved")
    if (
        cancel.get("record_id") != BENEFITS_ID
        or cancel.get("before_status") != "tentative"
        or cancel.get("after_status") != "cancelled"
        or cancel.get("reason") != REASON
        or cancel.get("cancelled") is not True
    ):
        fail("the conditional cancellation was not exactly the permitted mutation")

    notice = events[5]
    notice_interval = require_interval(notice, "notification event")
    if notice_interval[0] <= cancel_interval[1]:
        fail("the notification started before cancellation succeeded")
    if (
        notice.get("recipient") != "scheduling desk"
        or notice.get("record_id") != BENEFITS_ID
        or notice.get("notification_sha256") != notification_digest(notification)
    ):
        fail("the notification audit does not match the single permitted notice")

    print(
        "verification passed: parallel search and retrieval layers preceded the "
        "single Benefits cancellation and dependent scheduling-desk notice"
    )


if __name__ == "__main__":
    main()
