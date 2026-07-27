#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0203."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".hospitality"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
CEDAR_ID = "hos-303"
HARBOR_ID = "hos-703"


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
        fail(f"{label} contains an invalid reservation")
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


def require_separate_invocations(
    events: list[dict[str, Any]], label: str
) -> None:
    invocation_contexts = [
        (event.get("invocation_namespace"), event.get("parent_process_id"))
        for event in events
    ]
    if any(
        not isinstance(invocation_namespace, int)
        or isinstance(invocation_namespace, bool)
        or invocation_namespace <= 0
        or not isinstance(parent_process_id, int)
        or isinstance(parent_process_id, bool)
        or parent_process_id <= 0
        for invocation_namespace, parent_process_id in invocation_contexts
    ):
        fail(f"{label} lack valid invocation evidence")
    if len(set(invocation_contexts)) != len(invocation_contexts):
        fail(f"{label} were combined in one Bash invocation")


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_unchanged_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if current != initial:
        changed = sorted(
            record_id
            for record_id in set(current) | set(initial)
            if current.get(record_id) != initial.get(record_id)
        )
        fail("reservation state changed: " + ", ".join(changed))
    if load_object(STATE_DIR / "notifications.json") != load_object(
        INITIAL_NOTIFICATIONS_PATH
    ):
        fail("notification state changed")


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Cedar Room workshop", "Austin"): [CEDAR_ID],
        ("Harbor Suite reunion", "Raleigh"): [HARBOR_ID],
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 2):
        scope = (event.get("name"), event.get("location"))
        if scope in observed:
            fail("a required search was duplicated")
        observed[scope] = event.get("result_ids")
        intervals.append(require_interval(event, f"search event {index}"))
    if observed != expected:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    require_separate_invocations(events, "the two searches")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two searches did not execute concurrently")
    return intervals


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_ids = {CEDAR_ID, HARBOR_ID}
    observed: dict[Any, tuple[Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 4):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete reservation")
        record_id = event.get("record_id")
        if record_id in observed:
            fail("a required retrieval was duplicated")
        observed[record_id] = (
            event.get("record_sha256"),
            event.get("date"),
            event.get("status"),
        )
        intervals.append(require_interval(event, f"retrieval event {index}"))
    expected = {
        record_id: (
            record_digest(initial[record_id]),
            initial[record_id].get("date"),
            initial[record_id].get("status"),
        )
        for record_id in expected_ids
    }
    if observed != expected:
        fail("retrievals did not return both complete records found by search")
    require_separate_invocations(events, "the two retrievals")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def main() -> None:
    initial = record_map(load_object(INITIAL_RECORDS_PATH), "protected initial records")
    if CEDAR_ID not in initial or HARBOR_ID not in initial:
        fail("protected target fixtures are missing")
    verify_unchanged_state(initial)

    events = load_audit()
    if len(events) != 5:
        fail(
            "expected one help invocation and exactly four hospitality operations, "
            f"found {len(events)} total events"
        )
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if events[0].get("operation") != "help":
        fail("the executable help was not invoked first")
    if [event.get("operation") for event in events[1:3]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[3:]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a hospitality operation failed")

    help_interval = require_interval(events[0], "help event 1")
    require_separate_invocations(events[:1], "the help invocation")
    search_intervals = verify_searches(events[1:3])
    get_intervals = verify_gets(events[3:], initial)
    if min(interval[0] for interval in search_intervals) <= help_interval[1]:
        fail("a search started before help completed")
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    print(
        "verification passed: help preceded separate concurrent exact searches and separate concurrent complete retrievals; reservation and notification state stayed unchanged"
    )


if __name__ == "__main__":
    main()
