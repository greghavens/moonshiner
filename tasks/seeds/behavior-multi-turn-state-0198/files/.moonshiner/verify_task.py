#!/usr/bin/env python3
"""Protected verification for behavior-multi-turn-state-0198."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INITIAL_RECORDS = ROOT / ".moonshiner" / "initial_records.json"
INITIAL_NOTIFICATIONS = ROOT / ".moonshiner" / "initial_notifications.json"
KEY = ROOT / ".moonshiner" / "audit.key"
RECORDS = ROOT / "state" / "records.json"
NOTIFICATIONS = ROOT / "state" / "notifications.json"
AUDIT = ROOT / "state" / "audit.jsonl"
TARGET_ID = "SUB-6674"
COMPARISON_ID = "SUB-9674"
FIRST_DATE = "2026-11-21"
CORRECTED_DATE = "2026-12-25"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_events() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in AUDIT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_seals(events: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    key = KEY.read_bytes().strip()
    for sequence, event in enumerate(events, 1):
        if event.get("sequence") != sequence:
            failures.append(f"audit sequence is invalid at event {sequence}")
        unsigned = dict(event)
        seal = unsigned.pop("seal", None)
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(seal, expected):
            failures.append(
                f"audit event {sequence} was not emitted intact by subscriptions"
            )
    return failures


def interval(event: dict[str, Any]) -> tuple[int, int] | None:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
    ):
        return None
    return started, finished


def verify() -> list[str]:
    failures: list[str] = []
    try:
        initial = load_json(INITIAL_RECORDS)
        current = load_json(RECORDS)
        initial_notifications = load_json(INITIAL_NOTIFICATIONS)
        notifications = load_json(NOTIFICATIONS)
        events = read_events()
    except (OSError, json.JSONDecodeError) as error:
        return [f"workspace state is unreadable: {error}"]

    failures.extend(validate_seals(events))

    if notifications != initial_notifications:
        failures.append("notification state changed")

    initial_by_id = {
        record.get("id"): record for record in initial if isinstance(record, dict)
    }
    current_by_id = {
        record.get("id"): record for record in current if isinstance(record, dict)
    }
    if (
        len(initial_by_id) != len(initial)
        or len(current_by_id) != len(current)
        or list(initial_by_id) != list(current_by_id)
    ):
        failures.append("subscriptions were created, deleted, duplicated, or reordered")
        return failures

    expected = json.loads(json.dumps(initial))
    expected_target = next(
        record for record in expected if record["id"] == TARGET_ID
    )
    expected_target["renewal_date"] = CORRECTED_DATE
    if current != expected:
        failures.append(
            "final records differ from the corrected target-only renewal change"
        )
    for record_id, original in initial_by_id.items():
        if record_id != TARGET_ID and current_by_id.get(record_id) != original:
            failures.append(f"untargeted subscription changed: {record_id}")
    if current_by_id.get(COMPARISON_ID) != initial_by_id.get(COMPARISON_ID):
        failures.append("the comparison subscription changed")

    operations = [event.get("operation") for event in events]
    if operations != ["get", "get", "update-renewal", "update-renewal"]:
        failures.append(
            "required reads or corrections are missing, reordered, or accompanied by an extra operation"
        )
        return failures

    gets = events[:2]
    first_update, correction = events[2:]
    if {event.get("record_id") for event in gets} != {
        TARGET_ID,
        COMPARISON_ID,
    }:
        failures.append("the concurrent read did not retrieve exactly both requested IDs")
    batches = {event.get("batch") for event in gets}
    if len(batches) != 1 or None in batches:
        failures.append("the two records were not retrieved in one batch")
    get_intervals = [interval(event) for event in gets]
    if any(value is None for value in get_intervals):
        failures.append("a retrieval has an invalid execution interval")
    elif max(value[0] for value in get_intervals) >= min(
        value[1] for value in get_intervals
    ):
        failures.append("the two independent retrievals did not execute concurrently")
    for event in gets:
        original = initial_by_id.get(event.get("record_id"))
        if (
            original is None
            or event.get("found") is not True
            or event.get("record_sha256") != digest(original)
        ):
            failures.append(
                f"retrieval evidence is incomplete for {event.get('record_id')}"
            )

    first_interval = interval(first_update)
    correction_interval = interval(correction)
    if first_interval is None or correction_interval is None:
        failures.append("an update has an invalid execution interval")
    else:
        if get_intervals and all(value is not None for value in get_intervals):
            if max(value[1] for value in get_intervals) > first_interval[0]:
                failures.append("the first update began before both reads completed")
        if first_interval[1] > correction_interval[0]:
            failures.append("the correction did not follow the first update")

    original_target = initial_by_id.get(TARGET_ID, {})
    if (
        first_update.get("record_id") != TARGET_ID
        or first_update.get("before_date")
        != original_target.get("renewal_date")
        or first_update.get("after_date") != FIRST_DATE
        or first_update.get("outcome") != "ok"
    ):
        failures.append("the first renewal instruction was not applied to SUB-6674")
    if (
        correction.get("record_id") != TARGET_ID
        or correction.get("before_date") != FIRST_DATE
        or correction.get("after_date") != CORRECTED_DATE
        or correction.get("outcome") != "ok"
    ):
        failures.append("the corrected renewal date did not supersede the first date")

    return failures


def main() -> int:
    failures = verify()
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(
        "PASS: both subscriptions were read together; SUB-6674's first date "
        "was superseded by 2026-12-25; all other state stayed unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
