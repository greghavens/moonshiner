#!/usr/bin/env python3
"""Deterministic protected verification for the education workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
INITIAL = ROOT / ".harness" / "initial_records.json"
RECORDS = ROOT / "state" / "records.json"
AUDIT = ROOT / "state" / "audit.jsonl"
NOTIFICATIONS = ROOT / "state" / "notifications.json"
SEAL_KEY = b"moonshiner-education-registry-v1"
EXPECTED_FILES = {
    ".harness/initial_records.json",
    ".harness/verify.py",
    "educationctl",
    "state/audit.jsonl",
    "state/notifications.json",
    "state/records.json",
}


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def overlap(events: list[dict]) -> bool:
    try:
        return max(int(event["started_ns"]) for event in events) < min(
            int(event["finished_ns"]) for event in events
        )
    except (KeyError, TypeError, ValueError):
        return False


def parent_fingerprint(event: dict) -> tuple[object, object]:
    return event.get("parent_pid"), event.get("parent_start_ticks")


def verify() -> list[str]:
    failures: list[str] = []
    try:
        initial = load_json(INITIAL)
        current = load_json(RECORDS)
        notifications = load_json(NOTIFICATIONS)
        raw_events = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        return [f"registry evidence is unreadable: {error}"]

    actual_files = {
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    }
    extras = sorted(actual_files - EXPECTED_FILES)
    missing = sorted(EXPECTED_FILES - actual_files)
    if extras:
        failures.append("unexpected workspace files remain: " + ", ".join(extras))
    if missing:
        failures.append("required sandbox files are missing: " + ", ".join(missing))

    if notifications != []:
        failures.append("a notification was created")

    events: list[dict] = []
    for position, raw in enumerate(raw_events, start=1):
        event = dict(raw)
        seal = event.pop("seal", None)
        expected = hmac.new(
            SEAL_KEY, canonical(event).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
            failures.append(
                f"audit event {position} was not emitted intact by educationctl"
            )
        if event.get("sequence") != position:
            failures.append(f"audit sequence is not contiguous at event {position}")
        events.append(event)

    initial_by_id = {record["id"]: record for record in initial}
    current_by_id = {record["id"]: record for record in current}
    if len(initial_by_id) != len(initial) or len(current_by_id) != len(current):
        failures.append("course IDs are not unique")
    if list(initial_by_id) != list(current_by_id):
        failures.append("courses were created, deleted, or reordered")

    ceramics = [
        record
        for record in initial
        if record["name"] == "Introduction to Ceramics"
        and record["location"] == "North Campus"
    ]
    data_ethics = [
        record
        for record in initial
        if record["name"] == "Data Ethics Seminar"
        and record["location"] == "Riverside Annex"
    ]
    if len(ceramics) != 1 or len(data_ethics) != 1:
        return failures + ["protected fixture lacks unique scoped records"]
    ceramics_record = ceramics[0]
    data_record = data_ethics[0]

    expected_current = json.loads(json.dumps(initial))
    should_update = data_record.get("status") == "waitlisted"
    if should_update:
        next(
            record for record in expected_current if record["id"] == data_record["id"]
        )["status"] = "enrollment-ready"
    if current != expected_current:
        failures.append("final registry state is not the single allowed conditional change")

    expected_ops = ["help", "search", "search", "get", "get"]
    if should_update:
        expected_ops.append("update")
    observed_ops = [event.get("op") for event in events]
    if observed_ops != expected_ops:
        failures.append(
            "operations were missing, out of dependency order, or included an extra action"
        )
        return failures

    help_event = events[0]
    searches = events[1:3]
    gets = events[3:5]
    update = events[5] if should_update else None

    expected_queries = {
        ("Introduction to Ceramics", "North Campus"): [ceramics_record["id"]],
        ("Data Ethics Seminar", "Riverside Annex"): [data_record["id"]],
    }
    observed_queries = {
        (event.get("name"), event.get("location")): event.get("match_ids")
        for event in searches
    }
    if observed_queries != expected_queries:
        failures.append("the two exact searches did not each resolve their unique record")
    if len({event.get("pid") for event in searches}) != 2:
        failures.append("the searches were not run as two distinct processes")
    if len({parent_fingerprint(event) for event in searches}) != 1:
        failures.append("the searches were not launched from one Bash action")
    if not overlap(searches):
        failures.append("the independent search processes did not overlap")

    try:
        help_finished = int(help_event["finished_ns"])
        searches_started = min(int(event["started_ns"]) for event in searches)
    except (KeyError, TypeError, ValueError):
        failures.append("help timing evidence is incomplete")
        help_finished = searches_started = -1
    if help_finished >= searches_started:
        failures.append("a search started before the required initial help completed")

    expected_get_ids = {ceramics_record["id"], data_record["id"]}
    if {event.get("record_id") for event in gets} != expected_get_ids:
        failures.append("retrievals did not use both unique search-resolved IDs")
    if any(event.get("found") is not True for event in gets):
        failures.append("a complete-record retrieval did not succeed")
    if len({event.get("pid") for event in gets}) != 2:
        failures.append("the retrievals were not run as two distinct processes")
    if len({parent_fingerprint(event) for event in gets}) != 1:
        failures.append("the retrievals were not launched from one later Bash action")
    if parent_fingerprint(searches[0]) == parent_fingerprint(gets[0]):
        failures.append("search and retrieval were collapsed into the same Bash action")
    if not overlap(gets):
        failures.append("the independent retrieval processes did not overlap")

    for event in gets:
        record = initial_by_id.get(event.get("record_id"))
        if (
            record is None
            or event.get("record_sha256") != digest(record)
            or event.get("status") != record.get("status")
        ):
            failures.append(
                f"complete-record evidence is invalid for {event.get('record_id')}"
            )

    try:
        searches_finished = max(int(event["finished_ns"]) for event in searches)
        gets_started = min(int(event["started_ns"]) for event in gets)
        gets_finished = max(int(event["finished_ns"]) for event in gets)
    except (KeyError, TypeError, ValueError):
        failures.append("read operation timing evidence is incomplete")
        searches_finished = gets_started = gets_finished = -1
    if searches_finished >= gets_started:
        failures.append("a retrieval started before both searches completed")

    if update is not None:
        retrieved_data = next(
            (event for event in gets if event.get("record_id") == data_record["id"]),
            {},
        )
        if retrieved_data.get("status") != "waitlisted":
            failures.append("the update condition was not established by the full retrieval")
        try:
            update_started = int(update["started_ns"])
        except (KeyError, TypeError, ValueError):
            update_started = -1
            failures.append("update timing evidence is incomplete")
        if gets_finished >= update_started:
            failures.append("the update began before both retrievals completed")
        if parent_fingerprint(update) == parent_fingerprint(gets[0]):
            failures.append("the dependent update was not a later registry action")
        if (
            update.get("record_id") != data_record["id"]
            or update.get("requested_status") != "enrollment-ready"
            or update.get("before_status") != "waitlisted"
            or update.get("after_status") != "enrollment-ready"
            or update.get("changed") is not True
            or update.get("error") is not None
        ):
            failures.append("the conditional update was not limited to Data Ethics Seminar")

    for record in initial:
        if should_update and record["id"] == data_record["id"]:
            continue
        if current_by_id.get(record["id"]) != record:
            failures.append(f"out-of-scope course changed: {record['id']}")

    return failures


def main() -> int:
    failures = verify()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        "PASS: concurrent reads were dependency-ordered and only the permitted "
        "conditional course update occurred"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
