#!/usr/bin/env python3
"""Protected verifier for behavior-error-recovery-0060."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".fleet"
PROTECTED_DIR = ROOT / ".protected"
KEY_PATH = PROTECTED_DIR / "audit.key"
SHUTTLE_SCOPE = ("Shuttle 30", "Depot D", "2026-11-15")
VAN_SCOPE = ("Van 12", "Depot A", "2026-11-15")


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        try:
            label = path.relative_to(ROOT)
        except ValueError:
            label = path
        fail(f"cannot read {label}: {error}")


def load_object(path: Path) -> dict[str, Any]:
    value = load_json(path)
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


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


def scope_of(event: dict[str, Any]) -> tuple[Any, Any, Any]:
    return event.get("name"), event.get("location"), event.get("date")


def records_by_id(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
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
        fail(f"{label} contains duplicate IDs")
    return result


def expected_availability(
    scope: tuple[str, str, str],
    records: dict[str, dict[str, Any]],
    reservations_document: dict[str, Any],
) -> tuple[str, bool]:
    name, location, date = scope
    matches = [
        record
        for record in records.values()
        if record.get("name") == name and record.get("location") == location
    ]
    if len(matches) != 1:
        fail(f"protected fixture does not uniquely resolve {name} at {location}")
    reservations = reservations_document.get("reservations")
    if reservations_document.get("version") != 1 or not isinstance(reservations, list):
        fail("protected reservation fixture has an invalid shape")
    record = matches[0]
    reserved = any(
        isinstance(reservation, dict)
        and reservation.get("vehicle_id") == record["id"]
        and reservation.get("date") == date
        and reservation.get("status") == "confirmed"
        for reservation in reservations
    )
    return record["id"], record.get("status") == "active" and not reserved


def verify_immutable_state() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    comparisons = (
        ("records.json", "initial_records.json", "fleet records"),
        ("reservations.json", "initial_reservations.json", "reservations"),
        ("notifications.json", "initial_notifications.json", "notifications"),
    )
    for current_name, initial_name, label in comparisons:
        current = load_object(STATE_DIR / current_name)
        initial = load_object(PROTECTED_DIR / initial_name)
        if current != initial:
            fail(f"{label} changed")
    initial_records = records_by_id(
        load_object(PROTECTED_DIR / "initial_records.json"), "protected records"
    )
    initial_reservations = load_object(PROTECTED_DIR / "initial_reservations.json")
    return initial_records, initial_reservations


def verify_failure_consumption() -> None:
    initial = load_object(PROTECTED_DIR / "initial_failures.json")
    current = load_object(STATE_DIR / "failures.json")
    expected = json.loads(json.dumps(initial))
    rules = expected.get("rules")
    if expected.get("version") != 1 or not isinstance(rules, list):
        fail("protected transient-failure fixture has an invalid shape")
    matching = [
        rule
        for rule in rules
        if isinstance(rule, dict)
        and (rule.get("name"), rule.get("location"), rule.get("date"))
        == SHUTTLE_SCOPE
    ]
    if len(matching) != 1 or matching[0].get("remaining") != 1:
        fail("protected transient-failure fixture is invalid")
    matching[0]["remaining"] = 0
    if current != expected:
        fail("the one-shot transient failure was not consumed exactly once")


def verify_success(
    event: dict[str, Any],
    scope: tuple[str, str, str],
    attempt: int,
    records: dict[str, dict[str, Any]],
    reservations: dict[str, Any],
) -> None:
    record_id, available = expected_availability(scope, records, reservations)
    if (
        scope_of(event) != scope
        or event.get("attempt") != attempt
        or event.get("outcome") != "ok"
        or event.get("record_id") != record_id
        or event.get("available") is not available
    ):
        fail(f"the successful result for {scope[0]} is incorrect")


def main() -> None:
    records, reservations = verify_immutable_state()
    verify_failure_consumption()

    events = load_audit()
    if len(events) != 3:
        fail(f"expected exactly three fleet operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if any(event.get("operation") != "availability" for event in events):
        fail("an operation other than availability was performed")

    initial = events[:2]
    if {scope_of(event) for event in initial} != {SHUTTLE_SCOPE, VAN_SCOPE}:
        fail("the initial layer was not exactly the two requested checks")
    first_intervals = [
        require_interval(event, f"initial event {index}")
        for index, event in enumerate(initial, 1)
    ]
    if max(interval[0] for interval in first_intervals) >= min(
        interval[1] for interval in first_intervals
    ):
        fail("the two initial availability checks did not execute concurrently")

    shuttle_initial = next(
        event for event in initial if scope_of(event) == SHUTTLE_SCOPE
    )
    van_initial = next(event for event in initial if scope_of(event) == VAN_SCOPE)
    if (
        shuttle_initial.get("attempt") != 1
        or shuttle_initial.get("outcome") != "transient_error"
        or shuttle_initial.get("error_code") != "temporary_unavailable"
        or "available" in shuttle_initial
    ):
        fail("the failed initial Shuttle check was not the expected transient error")
    verify_success(van_initial, VAN_SCOPE, 1, records, reservations)

    retry = events[2]
    retry_interval = require_interval(retry, "retry event")
    if retry_interval[0] <= max(interval[1] for interval in first_intervals):
        fail("the retry started before both initial checks completed")
    verify_success(retry, SHUTTLE_SCOPE, 2, records, reservations)

    print(
        "verification passed: the independent checks overlapped, only the transient Shuttle branch was retried, and fleet state stayed unchanged"
    )


if __name__ == "__main__":
    main()
