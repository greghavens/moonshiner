#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0183."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".reservations"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"
KEY_PATH = ROOT / ".harness" / "audit.key"
EXECUTABLE_PATH = ROOT / "reservation-registry"
EXPECTED_EXECUTABLE_SHA256 = "6a593360c1c1c1021d510263ad25d641ef485078283af70be72ddf25376709d3"
EXPECTED_INITIAL_RECORDS_SHA256 = "b476043084f94fe6bfae80a0a68b449260bf75dfeda6d8f843f5915b11dc6511"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_AUDIT_KEY_SHA256 = "ed923d28104b36b67d5d560eea61f127d76641285943d6598368e64f8cffee85"
HARBOR_ID = "hos-283"
MAGNOLIA_ID = "hos-683"
CANCELLATION_REASON = "Guest chose a different reservation"
NOTICE_MESSAGE = (
    "Magnolia Suite lodging in Raleigh was cancelled. "
    "Reason: Guest chose a different reservation."
)


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
    if document.get("version") != 1 or not isinstance(document.get("records"), list):
        fail(f"{label} has an invalid shape")
    records = document["records"]
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and record["id"]
        for record in records
    ):
        fail(f"{label} contains an invalid record")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains duplicate IDs")
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


def require_protected_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def main() -> None:
    require_protected_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "reservation executable"
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
        fail("reservations were created or deleted")
    if initial.get(MAGNOLIA_ID, {}).get("status") != "held":
        fail("protected Magnolia Suite lodging fixture is invalid")

    for record_id, original in initial.items():
        if record_id == MAGNOLIA_ID:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = CANCELLATION_REASON
            if current[record_id] != expected:
                fail("Magnolia Suite lodging was not cancelled exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted reservation {record_id} was modified")
    if current[HARBOR_ID] != initial[HARBOR_ID]:
        fail("Harbor Room seminar was modified")

    initial_notifications = load_json(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_json(STATE_DIR / "notifications.json")
    if current_notifications != {
        "version": 1,
        "notifications": [
            {
                "ordinal": 1,
                "recipient": "reservation desk",
                "record_id": MAGNOLIA_ID,
                "message": NOTICE_MESSAGE,
            }
        ],
    }:
        fail("reservation-desk notification state is missing, extra, or incorrect")
    if initial_notifications != {"version": 1, "notifications": []}:
        fail("protected initial notifications fixture is invalid")

    events = load_audit()
    if len(events) != 6:
        fail(f"expected exactly six registry operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first operation layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second operation layer was not exactly two retrievals")
    if events[4].get("operation") != "cancel":
        fail("the fifth registry operation was not the conditional cancellation")
    if events[5].get("operation") != "notify":
        fail("the notice did not immediately follow the cancellation")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a registry operation failed or was skipped")

    expected_searches = {
        ("Harbor Room seminar", "Austin"): [HARBOR_ID],
        ("Magnolia Suite lodging", "Raleigh"): [MAGNOLIA_ID],
    }
    actual_searches: dict[tuple[Any, Any], Any] = {}
    search_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        scope = (event.get("name"), event.get("location"))
        if scope in actual_searches:
            fail("a required search was duplicated")
        actual_searches[scope] = event.get("result_ids")
        search_intervals.append(require_interval(event, f"search event {index}"))
    if actual_searches != expected_searches:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(search_intervals[0], search_intervals[1]):
        fail("the two searches did not execute concurrently")

    expected_gets = {
        HARBOR_ID: {
            "digest": record_digest(initial[HARBOR_ID]),
            "status": initial[HARBOR_ID]["status"],
        },
        MAGNOLIA_ID: {
            "digest": record_digest(initial[MAGNOLIA_ID]),
            "status": initial[MAGNOLIA_ID]["status"],
        },
    }
    actual_gets: dict[Any, dict[str, Any]] = {}
    get_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:4], 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = event.get("record_id")
        if record_id in actual_gets:
            fail("a required retrieval was duplicated")
        actual_gets[record_id] = {
            "digest": event.get("record_sha256"),
            "status": event.get("status"),
        }
        get_intervals.append(require_interval(event, f"retrieval event {index}"))
    if actual_gets != expected_gets:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(get_intervals[0], get_intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    cancellation = events[4]
    cancellation_interval = require_interval(cancellation, "cancellation event")
    if cancellation_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the cancellation started before both complete records were retrieved")
    if (
        cancellation.get("record_id") != MAGNOLIA_ID
        or cancellation.get("reason") != CANCELLATION_REASON
        or cancellation.get("before_status") != "held"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("cancelled") != 1
    ):
        fail("the conditional cancellation was not the one permitted")

    notice = events[5]
    notice_interval = require_interval(notice, "notification event")
    if notice_interval[0] <= cancellation_interval[1]:
        fail("the notice started before cancellation succeeded")
    if (
        notice.get("record_id") != MAGNOLIA_ID
        or notice.get("recipient") != "reservation desk"
        or notice.get("delivered") != 1
        or notice.get("message_sha256")
        != hashlib.sha256(NOTICE_MESSAGE.encode("utf-8")).hexdigest()
    ):
        fail("the post-cancellation notice was missing or incorrect")

    print(
        "verification passed: parallel searches and retrievals preceded the "
        "single confirmed Magnolia cancellation and its dependent desk notice"
    )


if __name__ == "__main__":
    main()
