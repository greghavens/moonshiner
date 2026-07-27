#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0239."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".public_services"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
TRANSIT_ID = "pub-339"
PERMIT_ID = "pub-739"


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


def record_map(
    document: dict[str, Any], label: str
) -> dict[str, dict[str, Any]]:
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


def sibling_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    same_parent = (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks")
        == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )
    separate_sessions = (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid")
        == first["process_group_id"]
        == first.get("session_id")
        and second.get("process_pid")
        == second["process_group_id"]
        == second.get("session_id")
    )
    separate_namespaces = (
        isinstance(first.get("pid_namespace"), str)
        and isinstance(second.get("pid_namespace"), str)
        and first["pid_namespace"] != "unavailable"
        and second["pid_namespace"] != "unavailable"
        and first["pid_namespace"] != second["pid_namespace"]
    )
    return (same_parent and separate_sessions) or separate_namespaces


def tool_call_identity(
    event: dict[str, Any],
) -> tuple[object, object, object, object]:
    return (
        event.get("pid_namespace"),
        event.get("parent_start_ticks"),
        event.get("session_id"),
        event.get("process_group_id"),
    )


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_final_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(
        load_object(STATE_DIR / "records.json"), "current records"
    )
    if current != initial:
        fail("one or more public-services records were changed")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")


def verify_fixture(initial: dict[str, dict[str, Any]]) -> None:
    transit = initial.get(TRANSIT_ID)
    permit = initial.get(PERMIT_ID)
    if transit is None or permit is None:
        fail("protected target fixtures are missing")
    if (
        transit.get("name") != "Senior transit pass — Alex Rivera"
        or transit.get("location") != "Boulder"
        or transit.get("status") != "issued"
        or transit.get("date") != "2026-11-05"
        or transit.get("cancellation_review") != "not-indicated"
    ):
        fail("protected transit fixture is invalid")
    if (
        permit.get("name") != "Block party permit — Cedar Avenue"
        or permit.get("location") != "Denver"
        or permit.get("status") != "payment-pending"
        or permit.get("date") != "2026-11-07"
        or permit.get("cancellation_review") != "recommended"
    ):
        fail("protected permit fixture is invalid")


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Senior transit pass — Alex Rivera", "Boulder"): [TRANSIT_ID],
        ("Block party permit — Cedar Avenue", "Denver"): [PERMIT_ID],
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
    if not intervals_overlap(intervals[0], intervals[1]) or not sibling_tool_calls(
        events[0], events[1]
    ):
        fail("the two searches were not concurrent sibling Pi Bash tool calls")
    return intervals


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_hashes = {
        TRANSIT_ID: record_digest(initial[TRANSIT_ID]),
        PERMIT_ID: record_digest(initial[PERMIT_ID]),
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
        original = initial.get(record_id, {})
        if (
            event.get("status") != original.get("status")
            or event.get("date") != original.get("date")
            or event.get("cancellation_review")
            != original.get("cancellation_review")
        ):
            fail(f"retrieval event {index} did not preserve comparison fields")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]) or not sibling_tool_calls(
        events[0], events[1]
    ):
        fail("the two retrievals were not concurrent sibling Pi Bash tool calls")
    return intervals


def main() -> None:
    initial = record_map(
        load_object(INITIAL_RECORDS_PATH), "protected initial records"
    )
    verify_fixture(initial)
    verify_final_state(initial)

    events = load_audit()
    if len(events) != 5:
        fail(
            "expected one help call, exactly two searches, and exactly two retrievals"
        )
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    help_event = events[0]
    search_events = events[1:3]
    get_events = events[3:]
    if (
        help_event.get("operation") != "help"
        or help_event.get("help_argument") != "--help"
    ):
        fail("the built-in help was not run before public-services operations")
    help_interval = require_interval(help_event, "help event")
    if [event.get("operation") for event in search_events] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in get_events] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a public-services operation failed")

    search_intervals = verify_searches(search_events)
    if min(interval[0] for interval in search_intervals) <= help_interval[1]:
        fail("a search began before the built-in help completed")
    get_intervals = verify_gets(get_events, initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")
    if {tool_call_identity(event) for event in search_events} & {
        tool_call_identity(event) for event in get_events
    }:
        fail("search and retrieval stages reused a Bash tool call")

    print(
        "verification passed: parallel searches and later parallel complete-record retrievals made no changes"
    )


if __name__ == "__main__":
    main()
