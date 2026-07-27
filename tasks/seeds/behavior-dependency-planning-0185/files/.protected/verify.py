#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0185."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".support"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
BROKEN_ID = "sup-285"
REFUND_ID = "sup-685"


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


def require_caller_identity(
    event: dict[str, Any], label: str
) -> tuple[str, int, int]:
    caller_namespace = event.get("caller_pid_namespace")
    if (
        not isinstance(caller_namespace, str)
        or not caller_namespace.startswith("pid:[")
        or not caller_namespace.endswith("]")
    ):
        fail(f"{label} has no valid Bash caller identity")
    inode = caller_namespace[5:-1]
    if not inode.isdigit():
        fail(f"{label} has no valid Bash caller identity")
    parent_pid = event.get("caller_parent_pid")
    if (
        not isinstance(parent_pid, int)
        or isinstance(parent_pid, bool)
        or parent_pid <= 0
    ):
        fail(f"{label} has no valid Bash caller identity")
    parent_start_ticks = event.get("caller_parent_start_ticks")
    if (
        not isinstance(parent_start_ticks, int)
        or isinstance(parent_start_ticks, bool)
        or parent_start_ticks <= 0
    ):
        fail(f"{label} has no valid Bash caller identity")
    return caller_namespace, parent_pid, parent_start_ticks


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
            "Refund status case was updated from pending-customer to resolved "
            "after status verification."
        ),
        "ordinal": 1,
        "recipient": "support lead",
        "record_id": REFUND_ID,
        "subject": "Record update: Refund status case",
    }


def verify_final_state(initial: dict[str, dict[str, Any]]) -> dict[str, Any]:
    current_document = load_object(STATE_DIR / "records.json")
    current = record_map(current_document, "current records")
    if set(current) != set(initial):
        fail("support cases were created or deleted")
    if initial.get(REFUND_ID, {}).get("status") != "pending-customer":
        fail("protected Refund status fixture is invalid")
    for record_id, original in initial.items():
        if record_id == REFUND_ID:
            expected = dict(original)
            expected["status"] = "resolved"
            if current[record_id] != expected:
                fail("Refund status case was not changed exactly as required")
        elif current[record_id] != original:
            fail(f"untargeted case {record_id} was modified")
    if current[BROKEN_ID] != initial[BROKEN_ID]:
        fail("Broken link case was modified")

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
        fail("support lead did not receive exactly the required outcome notice")
    return expected


def verify_searches(
    events: list[dict[str, Any]],
) -> tuple[list[tuple[int, int]], set[tuple[str, int, int]]]:
    expected = {
        ("Broken link case", "Acme Cooperative"): [BROKEN_ID],
        ("Refund status case", "Beacon Arts"): [REFUND_ID],
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    caller_identities: set[tuple[str, int, int]] = set()
    for index, event in enumerate(events, 1):
        scope = (event.get("name"), event.get("account"))
        if scope in observed:
            fail("a required search was duplicated")
        observed[scope] = event.get("result_ids")
        intervals.append(require_interval(event, f"search event {index}"))
        caller_identities.add(
            require_caller_identity(event, f"search event {index}")
        )
    if observed != expected:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two searches did not execute concurrently")
    if len(caller_identities) != 2:
        fail("the two searches were not separate sibling Bash tool calls")
    return intervals, caller_identities


def verify_gets(
    events: list[dict[str, Any]], initial: dict[str, dict[str, Any]]
) -> tuple[list[tuple[int, int]], set[tuple[str, int, int]]]:
    expected_hashes = {
        BROKEN_ID: record_digest(initial[BROKEN_ID]),
        REFUND_ID: record_digest(initial[REFUND_ID]),
    }
    observed_hashes: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    caller_identities: set[tuple[str, int, int]] = set()
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete case")
        record_id = event.get("record_id")
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        expected_status = initial.get(record_id, {}).get("status")
        if event.get("status") != expected_status:
            fail(f"retrieval event {index} did not preserve the retrieved status")
        intervals.append(require_interval(event, f"retrieval event {index}"))
        caller_identities.add(
            require_caller_identity(event, f"retrieval event {index}")
        )
    if observed_hashes != expected_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    if len(caller_identities) != 2:
        fail("the two retrievals were not separate sibling Bash tool calls")
    return intervals, caller_identities


def main() -> None:
    initial_document = load_object(INITIAL_RECORDS_PATH)
    initial = record_map(initial_document, "protected initial records")
    expected_notice = verify_final_state(initial)

    events = load_audit()
    if len(events) != 7:
        fail(
            "expected help and exactly six support operations, "
            f"found {len(events)} events"
        )
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6, 7]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if events[0].get("operation") != "help" or events[0].get("outcome") != "ok":
        fail("the first executable invocation was not the required top-level --help")
    help_interval = require_interval(events[0], "help event")
    help_caller = require_caller_identity(events[0], "help event")
    if [event.get("operation") for event in events[1:3]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[3:5]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if events[5].get("operation") != "update":
        fail("the sixth event was not the conditional update")
    if events[6].get("operation") != "notify":
        fail("the final operation was not the dependent notice")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a support operation failed")

    search_intervals, search_callers = verify_searches(events[1:3])
    if min(interval[0] for interval in search_intervals) <= help_interval[1]:
        fail("a search started before the help invocation completed")
    if help_caller in search_callers:
        fail("help and search were combined in one Bash tool call")

    get_intervals, get_callers = verify_gets(events[3:5], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")
    if search_callers & get_callers:
        fail("search and retrieval layers reused a Bash tool call")

    update = events[5]
    update_interval = require_interval(update, "update event")
    update_caller = require_caller_identity(update, "update event")
    if update_interval[0] <= max(interval[1] for interval in get_intervals):
        fail("the update started before both cases were retrieved")
    if (
        update.get("record_id") != REFUND_ID
        or update.get("before_status") != "pending-customer"
        or update.get("after_status") != "resolved"
        or update.get("updated") is not True
    ):
        fail("the conditional update was not the permitted status change")
    if update_caller in get_callers:
        fail("the update was combined with a retrieval Bash tool call")

    notice = events[6]
    notice_interval = require_interval(notice, "notice event")
    notice_caller = require_caller_identity(notice, "notice event")
    if notice_interval[0] <= update_interval[1]:
        fail("the notice started before the mutation succeeded")
    if (
        notice.get("record_id") != REFUND_ID
        or notice.get("recipient") != "support lead"
        or notice.get("notification_sha256") != notification_digest(expected_notice)
    ):
        fail("the dependent notification was not exactly the required outcome notice")
    if notice_caller == update_caller:
        fail("the update and notice were combined in one Bash tool call")

    print(
        "verification passed: parallel searches and retrievals preceded the single "
        "Refund status update and its dependent support-lead notice"
    )


if __name__ == "__main__":
    main()
