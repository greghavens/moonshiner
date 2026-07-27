#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0200."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".fleet"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "fleetctl"
REPORT_PATH = ROOT / "handoff.md"
EXPECTED_EXECUTABLE_SHA256 = "3b8428602676e7c79c3a091c1a4cd5183611b72e5366a3abb5143452690d4ca4"
EXPECTED_INITIAL_RECORDS_SHA256 = "bbf52c9cbd0bd415644a066145ee5175f3e47cbd8b411794098ede1a56d16613"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547"
EXPECTED_AUDIT_KEY_SHA256 = "bd3389c2dea9efd2faeb13ecd9fef4b02ca039fca6f3ba2c630e64f7c5ca3364"
SHUTTLE_ID = "fle-300"
VAN_ID = "fle-700"


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
                    fail(f"operation-history line {line_number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"operation-history line {line_number} is not an object")
                events.append(event)
    except OSError as error:
        fail(f"cannot read operation history: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"operation-history event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"operation-history event {index} has an invalid seal")


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


def verify_target_fixture(
    initial: dict[str, dict[str, Any]], record_id: str, name: str, location: str
) -> dict[str, Any]:
    record = initial.get(record_id)
    if record is None:
        fail(f"protected target fixture {record_id} is missing")
    if record.get("name") != name or record.get("location") != location:
        fail(f"protected target fixture {record_id} has the wrong scope")
    if not isinstance(record.get("status"), str) or not record["status"]:
        fail(f"protected target fixture {record_id} has no status")
    if not isinstance(record.get("date"), str) or not record["date"]:
        fail(f"protected target fixture {record_id} has no date")
    return record


def verify_searches(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    expected = {
        ("Shuttle 30 accessibility run", "Depot D"): [SHUTTLE_ID],
        ("Van 12 library route", "Depot A"): [VAN_ID],
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
    expected = {
        SHUTTLE_ID: record_digest(initial[SHUTTLE_ID]),
        VAN_ID: record_digest(initial[VAN_ID]),
    }
    observed: dict[Any, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = event.get("record_id")
        if record_id in observed:
            fail("a required retrieval was duplicated")
        observed[record_id] = event.get("record_sha256")
        record = initial.get(record_id, {})
        if event.get("status") != record.get("status"):
            fail(f"retrieval event {index} did not preserve the complete-record status")
        if event.get("date") != record.get("date"):
            fail(f"retrieval event {index} did not preserve the complete-record date")
        intervals.append(require_interval(event, f"retrieval event {index}"))
    if observed != expected:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")
    return intervals


def verify_report(shuttle: dict[str, Any], van: dict[str, Any]) -> None:
    expected = (
        f"- {shuttle['name']} | {shuttle['id']} | {shuttle['location']} | "
        f"{shuttle['status']} | {shuttle['date']}\n"
        f"- {van['name']} | {van['id']} | {van['location']} | "
        f"{van['status']} | {van['date']}\n"
        f"- Comparison: status {shuttle['status']} versus {van['status']}; "
        f"date {shuttle['date']} versus {van['date']}.\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read handoff.md: {error}")
    if actual != expected:
        fail("handoff.md is missing, includes unrelated content, or has incorrect values")


def main() -> None:
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "fleet executable")
    require_digest(
        INITIAL_RECORDS_PATH, EXPECTED_INITIAL_RECORDS_SHA256, "initial records"
    )
    require_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial_document = load_object(INITIAL_RECORDS_PATH)
    current_document = load_object(STATE_DIR / "records.json")
    initial = record_map(initial_document, "protected initial records")
    record_map(current_document, "current records")
    if current_document != initial_document:
        fail("fleet records were created, updated, cancelled, deleted, or reordered")
    if load_object(STATE_DIR / "notifications.json") != load_object(
        INITIAL_NOTIFICATIONS_PATH
    ):
        fail("notification state changed")

    shuttle = verify_target_fixture(
        initial, SHUTTLE_ID, "Shuttle 30 accessibility run", "Depot D"
    )
    van = verify_target_fixture(initial, VAN_ID, "Van 12 library route", "Depot A")

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four fleet operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("operation-history sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a fleet operation failed")

    search_intervals = verify_searches(events[:2])
    get_intervals = verify_gets(events[2:], initial)
    if min(interval[0] for interval in get_intervals) <= max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    verify_report(shuttle, van)
    print(
        "verification passed: two parallel exact searches preceded two parallel complete-record retrievals; fleet state stayed read-only"
    )


if __name__ == "__main__":
    main()
