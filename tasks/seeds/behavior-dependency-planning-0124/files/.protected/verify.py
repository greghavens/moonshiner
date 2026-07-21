#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0124."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".messaging"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
WEATHER_ID = "mes-224"
MUSEUM_ID = "mes-624"


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


def intervals_overlap(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_final_state(initial: dict[str, dict[str, Any]]) -> None:
    current_document = load_object(STATE_DIR / "records.json")
    current = record_map(current_document, "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    if initial.get(MUSEUM_ID, {}).get("status") != "draft":
        fail("protected Museum opening fixture is invalid")
    for record_id, original in initial.items():
        if record_id == MUSEUM_ID:
            expected = dict(original)
            expected["status"] = "scheduled"
            if current[record_id] != expected:
                fail("Museum opening announcement was not changed exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted record {record_id} was modified")
    if current[WEATHER_ID] != initial[WEATHER_ID]:
        fail("Weather closure notice was modified")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Weather closure notice", "North Team"): [WEATHER_ID],
        ("Museum opening announcement", "All Staff"): [MUSEUM_ID],
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        scope = (event.get("name"), event.get("channel"))
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
        WEATHER_ID: record_digest(initial[WEATHER_ID]),
        MUSEUM_ID: record_digest(initial[MUSEUM_ID]),
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
        expected_status = initial.get(record_id, {}).get("status")
        if event.get("status") != expected_status:
            fail(f"retrieval event {index} did not preserve the retrieved status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def main() -> None:
    initial_document = load_object(INITIAL_RECORDS_PATH)
    initial = record_map(initial_document, "protected initial records")
    verify_final_state(initial)

    events = load_audit()
    if len(events) != 5:
        fail(f"expected exactly five messaging operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if events[4].get("operation") != "update":
        fail("the final messaging operation was not the conditional update")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a messaging operation failed")

    search_intervals = verify_searches(events[:2])
    get_intervals = verify_gets(events[2:4], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    update = events[4]
    update_interval = require_interval(update, "update event")
    if update_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the update started before both records were retrieved")
    if (
        update.get("record_id") != MUSEUM_ID
        or update.get("before_status") != "draft"
        or update.get("after_status") != "scheduled"
        or update.get("updated") is not True
    ):
        fail("the conditional update was not exactly the permitted draft-to-scheduled change")

    print(
        "verification passed: parallel searches and retrievals preceded the single Museum opening status change"
    )


if __name__ == "__main__":
    main()
