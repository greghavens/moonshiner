#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-dependency-planning-0210."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".projects"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT_PATH = ROOT / "review_report.md"
TARGET_SCOPES = (
    ("Accessibility audit", "Beacon Program"),
    ("Newsletter redesign", "Clover Program"),
)


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
    result = {str(record["id"]): record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains duplicate IDs")
    return result


def target_records(initial: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    resolved: list[dict[str, Any]] = []
    for name, program in TARGET_SCOPES:
        matches = [
            record
            for record in initial.values()
            if record.get("name") == name and record.get("location") == program
        ]
        if len(matches) != 1:
            fail("protected records do not contain one unique match for each target")
        resolved.append(matches[0])
    return resolved


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


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
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


def overlap(first: tuple[int, int], second: tuple[int, int]) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_state(initial_document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    initial = record_map(initial_document, "protected initial records")
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if current != initial:
        fail("project records were created, changed, cancelled, or deleted")
    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")
    return initial


def verify_searches(
    events: list[dict[str, Any]], targets: list[dict[str, Any]]
) -> tuple[list[tuple[int, int]], set[str]]:
    expected = {
        (record["name"], record["location"]): [record["id"]]
        for record in targets
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 1):
        scope = (event.get("name"), event.get("program"))
        if scope in observed:
            fail("a required search was duplicated")
        observed[scope] = event.get("result_ids")
        intervals.append(interval(event, f"search event {index}"))
    if observed != expected:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not overlap(intervals[0], intervals[1]):
        fail("the two independent searches did not execute concurrently")
    returned_ids = {str(value[0]) for value in observed.values()}
    return intervals, returned_ids


def verify_gets(
    events: list[dict[str, Any]], targets: list[dict[str, Any]], returned_ids: set[str]
) -> list[tuple[int, int]]:
    expected_hashes = {
        str(record["id"]): record_digest(record) for record in targets
    }
    observed_hashes: dict[str, Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete record")
        record_id = str(event.get("record_id"))
        if record_id in observed_hashes:
            fail("a required retrieval was duplicated")
        observed_hashes[record_id] = event.get("record_sha256")
        expected_record = next(
            (record for record in targets if record["id"] == record_id), None
        )
        if expected_record is None:
            fail("an unrelated record was retrieved")
        if (
            event.get("status") != expected_record.get("status")
            or event.get("date") != expected_record.get("date")
        ):
            fail("retrieval evidence does not match the complete stored record")
        intervals.append(interval(event, f"retrieval event {index}"))
    if set(observed_hashes) != returned_ids or observed_hashes != expected_hashes:
        fail("retrievals did not use exactly the two unique search-result IDs")
    if not overlap(intervals[0], intervals[1]):
        fail("the two complete-record retrievals did not execute concurrently")
    return intervals


def relation(label: str, first: str, second: str) -> str:
    if first == second:
        return f"{label} match: {first}"
    return f"{label} differ: {first} vs {second}"


def verify_report(targets: list[dict[str, Any]]) -> None:
    if not REPORT_PATH.is_file():
        fail("review_report.md is missing")
    first, second = targets
    expected_lines = [
        "- "
        + " | ".join(
            str(first[field])
            for field in ("name", "id", "location", "status", "date")
        ),
        "- "
        + " | ".join(
            str(second[field])
            for field in ("name", "id", "location", "status", "date")
        ),
        "- Comparison | "
        + relation("statuses", str(first["status"]), str(second["status"]))
        + "; "
        + relation("dates", str(first["date"]), str(second["date"]))
        + ".",
    ]
    try:
        report = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"cannot read review_report.md: {error}")
    if report.splitlines() != expected_lines:
        fail("review_report.md is not the exact grounded three-line comparison")


def main() -> None:
    initial = verify_state(load_object(INITIAL_RECORDS_PATH))
    targets = target_records(initial)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four read-only project operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a required project operation failed")

    search_intervals, returned_ids = verify_searches(events[:2], targets)
    get_intervals = verify_gets(events[2:], targets, returned_ids)
    if min(item[0] for item in get_intervals) <= max(
        item[1] for item in search_intervals
    ):
        fail("a retrieval started before both unique searches completed")

    verify_report(targets)
    print("verification passed: dependency-ordered parallel read-only review verified")


if __name__ == "__main__":
    main()
