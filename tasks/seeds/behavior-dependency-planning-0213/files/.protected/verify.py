#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0213."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".library"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT_PATH = ROOT / "library_audit.md"
TARGET_SCOPES = {
    ("Oral Histories of Mesa County", "Archive Branch"),
    ("Beginner's Guide to Seed Saving", "Garden Branch"),
}


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
    required = {"id", "name", "location", "status", "date"}
    if not all(
        isinstance(record, dict)
        and required <= record.keys()
        and all(isinstance(record[field], str) and record[field] for field in required)
        for record in records
    ):
        fail(f"{label} contains an invalid record")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains duplicate IDs")
    return result


def target_records(initial: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records = [
        record
        for record in initial.values()
        if (record["name"], record["location"]) in TARGET_SCOPES
    ]
    if len(records) != 2:
        fail("protected target fixtures are invalid")
    return sorted(records, key=lambda record: record["name"])


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


def verify_unchanged_state(initial: dict[str, dict[str, Any]]) -> None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if current != initial:
        fail("library records changed during the read-only check")
    if load_object(STATE_DIR / "notifications.json") != load_object(
        INITIAL_NOTIFICATIONS_PATH
    ):
        fail("notification state changed during the read-only check")


def verify_searches(
    events: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    expected = {
        (record["name"], record["location"]): [record["id"]] for record in targets
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
    events: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    by_id = {record["id"]: record for record in targets}
    observed: dict[Any, tuple[Any, Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not find a complete record")
        record_id = event.get("record_id")
        if record_id in observed:
            fail("a required retrieval was duplicated")
        observed[record_id] = (
            event.get("record_sha256"),
            event.get("status"),
            event.get("date"),
        )
        intervals.append(require_interval(event, f"retrieval event {index}"))
    expected = {
        record_id: (record_digest(record), record["status"], record["date"])
        for record_id, record in by_id.items()
    }
    if observed != expected:
        fail("retrievals did not use both returned IDs and complete records")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two retrievals did not execute concurrently")
    return intervals


def expected_report(targets: list[dict[str, Any]]) -> str:
    first, second = targets
    status_relation = "match" if first["status"] == second["status"] else "differ"
    date_relation = "match" if first["date"] == second["date"] else "differ"
    entries = [
        f"- {record['name']} | {record['id']} | {record['location']} | "
        f"status: {record['status']} | date: {record['date']}"
        for record in targets
    ]
    entries.append(
        f"- Comparison | statuses: {first['status']} vs {second['status']} "
        f"({status_relation}) | dates: {first['date']} vs {second['date']} "
        f"({date_relation})"
    )
    return "\n".join(entries) + "\n"


def verify_report(targets: list[dict[str, Any]]) -> None:
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read library_audit.md: {error}")
    if actual != expected_report(targets):
        fail("library_audit.md is not the grounded, ordered three-bullet report")


def main() -> None:
    initial = record_map(load_object(INITIAL_RECORDS_PATH), "protected initial records")
    targets = target_records(initial)
    verify_unchanged_state(initial)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four library operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a library operation failed")

    search_intervals = verify_searches(events[:2], targets)
    get_intervals = verify_gets(events[2:], targets)
    if min(started for started, _ in get_intervals) <= max(
        finished for _, finished in search_intervals
    ):
        fail("a retrieval started before both searches completed")
    verify_report(targets)
    print("verification passed: read-only parallel library audit is complete")


if __name__ == "__main__":
    main()
