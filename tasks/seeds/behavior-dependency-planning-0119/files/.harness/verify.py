#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0119."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".municipal"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"
KEY_PATH = ROOT / ".harness" / "audit.key"
EXECUTABLE_PATH = ROOT / "municipal-records"
EXPECTED_EXECUTABLE_SHA256 = "c3f597d7d599d7410cfcaad6f49a45b4c6197a0c94239f1872a592f52193b4bb"
EXPECTED_INITIAL_RECORDS_SHA256 = "692a4cbf798a05292e1eaaa5475c70c7867c761a3fb6854337bb03cce54e3592"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_AUDIT_KEY_SHA256 = "da56fe852854a2f4de5c5b061f7f7a4c39203cc9d9c8a5b25c2b736aa1364f66"
RESIDENTIAL_ID = "mun-219"
TREE_ID = "mun-619"


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


def require_protected_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def main() -> None:
    try:
        executable_digest = hashlib.sha256(EXECUTABLE_PATH.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read municipal-records: {error}")
    if executable_digest != EXPECTED_EXECUTABLE_SHA256:
        fail("municipal-records was modified")

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

    if initial.get(TREE_ID, {}).get("status") != "submitted":
        fail("protected Tree-removal fixture is invalid")
    for record_id, original in initial.items():
        if record_id == TREE_ID:
            expected = dict(original)
            expected["status"] = "needs-information"
            if current[record_id] != expected:
                fail("Tree-removal application was not changed exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted record {record_id} was modified")
    if current[RESIDENTIAL_ID] != initial[RESIDENTIAL_ID]:
        fail("Residential parking permit was modified")

    initial_notifications = load_json(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_json(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("a notification was created or notification state changed")

    events = load_audit()
    if len(events) != 5:
        fail(f"expected exactly five registry operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first operation layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second operation layer was not exactly two retrievals")
    if events[4].get("operation") != "update":
        fail("the final registry operation was not the one conditional update")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a registry operation failed")

    expected_searches = {
        ("Residential parking permit", "Lakewood"): [RESIDENTIAL_ID],
        ("Tree-removal application", "Arvada"): [TREE_ID],
    }
    actual_searches: dict[tuple[Any, Any], Any] = {}
    search_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        scope = (event.get("name"), event.get("municipality"))
        if scope in actual_searches:
            fail("a required search was duplicated")
        actual_searches[scope] = event.get("result_ids")
        search_intervals.append(require_interval(event, f"search event {index}"))
    if actual_searches != expected_searches:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(search_intervals[0], search_intervals[1]):
        fail("the two searches did not execute concurrently")

    expected_get_hashes = {
        RESIDENTIAL_ID: record_digest(initial[RESIDENTIAL_ID]),
        TREE_ID: record_digest(initial[TREE_ID]),
    }
    actual_get_hashes: dict[Any, Any] = {}
    get_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:4], 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete record")
        record_id = event.get("record_id")
        if record_id in actual_get_hashes:
            fail("a required retrieval was duplicated")
        actual_get_hashes[record_id] = event.get("record_sha256")
        get_intervals.append(require_interval(event, f"retrieval event {index}"))
    if actual_get_hashes != expected_get_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(get_intervals[0], get_intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    update = events[4]
    update_interval = require_interval(update, "update event")
    if update_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the update started before both complete records were retrieved")
    if (
        update.get("record_id") != TREE_ID
        or update.get("before_status") != "submitted"
        or update.get("after_status") != "needs-information"
        or update.get("updated") != 1
    ):
        fail("the conditional update was not the one explicitly permitted")

    print(
        "verification passed: parallel searches and retrievals preceded the single confirmed Tree-removal status change"
    )


if __name__ == "__main__":
    main()
