#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0147."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".equipment"
PROTECTED_DIR = ROOT / ".protected"
SCOPES = [
    ("Folding event chairs", "Supply Room 2"),
    ("Portable whiteboards", "North Depot"),
]


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


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
        fail(f"{label} contains duplicate reservation IDs")
    return result


def profile_from(document: dict[str, Any], label: str) -> dict[str, Any]:
    profile = document.get("profile")
    if document.get("version") != 1 or not isinstance(profile, dict):
        fail(f"{label} has an invalid shape")
    default_date = profile.get("default_date")
    if not isinstance(default_date, str) or not default_date:
        fail(f"{label} has no default date")
    return profile


def option_list(document: dict[str, Any], label: str) -> list[dict[str, Any]]:
    options = document.get("options")
    if document.get("version") != 1 or not isinstance(options, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(option, dict)
        and isinstance(option.get("asset_id"), str)
        and bool(option["asset_id"])
        and isinstance(option.get("available"), bool)
        for option in options
    ):
        fail(f"{label} contains an invalid option")
    identifiers = [option["asset_id"] for option in options]
    if len(set(identifiers)) != len(identifiers):
        fail(f"{label} contains duplicate asset IDs")
    return options


def exact_option(
    options: list[dict[str, Any]], item: str, location: str, date: str
) -> dict[str, Any]:
    matches = [
        option
        for option in options
        if option.get("item") == item
        and option.get("location") == location
        and option.get("date") == date
    ]
    if len(matches) != 1:
        fail(f"protected availability is ambiguous for {item} in {location}")
    return matches[0]


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
        key = (PROTECTED_DIR / "audit.key").read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    if not key:
        fail("protected audit key is empty")
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


def protected_inputs() -> tuple[
    dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]
]:
    initial_profile_document = load_object(PROTECTED_DIR / "initial_profile.json")
    initial_profile = profile_from(initial_profile_document, "protected profile")
    initial_availability_document = load_object(
        PROTECTED_DIR / "initial_availability.json"
    )
    initial_options = option_list(
        initial_availability_document, "protected availability"
    )
    initial_records = record_map(
        load_object(PROTECTED_DIR / "initial_records.json"),
        "protected initial records",
    )
    initial_notifications = load_object(
        PROTECTED_DIR / "initial_notifications.json"
    )

    if load_object(STATE_DIR / "profile.json") != initial_profile_document:
        fail("operational profile state changed")
    if load_object(STATE_DIR / "availability.json") != initial_availability_document:
        fail("availability state changed")
    if load_object(STATE_DIR / "notifications.json") != initial_notifications:
        fail("notification state changed")
    return initial_profile, initial_options, initial_records


def expected_options(
    profile: dict[str, Any], options: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    date = profile["default_date"]
    scoped = [exact_option(options, item, location, date) for item, location in SCOPES]
    selected = next((option for option in scoped if option["available"]), None)
    return scoped, selected


def verify_final_records(
    initial: dict[str, dict[str, Any]], selected: dict[str, Any] | None
) -> dict[str, Any] | None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if not set(initial).issubset(current):
        fail("an initial reservation was deleted")
    for reservation_id, original in initial.items():
        if current[reservation_id] != original:
            fail(f"initial reservation {reservation_id} was modified")
    new_ids = set(current) - set(initial)
    expected_new_count = 1 if selected is not None else 0
    if len(new_ids) != expected_new_count:
        fail(
            f"expected {expected_new_count} new reservation(s), found {len(new_ids)}"
        )
    if selected is None:
        return None
    created = current[next(iter(new_ids))]
    expected = {
        "asset_id": selected["asset_id"],
        "created_at": f"{selected['date']}T14:00:00Z",
        "date": selected["date"],
        "id": created.get("id"),
        "item": selected["item"],
        "location": selected["location"],
        "quantity": 1,
        "status": "reserved",
    }
    if created != expected:
        fail("the new reservation does not exactly match the first available option")
    return created


def verify_profile_event(
    event: dict[str, Any], profile: dict[str, Any]
) -> tuple[int, int]:
    if (
        event.get("operation") != "profile"
        or event.get("outcome") != "ok"
        or event.get("default_date") != profile["default_date"]
        or event.get("profile_sha256") != digest(profile)
    ):
        fail("the first operation did not retrieve the protected operational profile")
    return require_interval(event, "profile event")


def verify_check_events(
    events: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_by_scope = {
        (option["item"], option["location"], option["date"]): option
        for option in expected
    }
    observed: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 2):
        if event.get("operation") != "availability" or event.get("outcome") != "ok":
            fail(f"audit event {index} was not a successful availability check")
        scope = (event.get("item"), event.get("location"), event.get("date"))
        if scope in observed:
            fail("a required availability check was duplicated")
        observed[scope] = event
        intervals.append(require_interval(event, f"availability event {index}"))
    if set(observed) != set(expected_by_scope):
        fail("availability checks were broad, incorrect, or incomplete")
    for scope, option in expected_by_scope.items():
        event = observed[scope]
        if (
            event.get("match_count") != 1
            or event.get("asset_id") != option["asset_id"]
            or event.get("available") is not option["available"]
            or event.get("option_sha256") != digest(option)
        ):
            fail("an availability result does not match protected registry data")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two availability checks did not execute concurrently")
    return intervals


def verify_reserve_event(
    event: dict[str, Any], selected: dict[str, Any], created: dict[str, Any]
) -> tuple[int, int]:
    before_count = event.get("before_count")
    after_count = event.get("after_count")
    if (
        event.get("operation") != "reserve"
        or event.get("outcome") != "ok"
        or event.get("asset_id") != selected["asset_id"]
        or event.get("date") != selected["date"]
        or event.get("quantity") != 1
        or event.get("reservation_id") != created["id"]
        or event.get("record_sha256") != digest(created)
        or not isinstance(before_count, int)
        or isinstance(before_count, bool)
        or not isinstance(after_count, int)
        or isinstance(after_count, bool)
        or before_count + 1 != after_count
    ):
        fail("the final operation was not the one permitted reservation creation")
    return require_interval(event, "reserve event")


def main() -> None:
    profile, options, initial_records = protected_inputs()
    scoped_options, selected = expected_options(profile, options)
    created = verify_final_records(initial_records, selected)

    events = load_audit()
    expected_event_count = 4 if selected is not None else 3
    if len(events) != expected_event_count:
        fail(
            f"expected exactly {expected_event_count} registry operations, "
            f"found {len(events)}"
        )
    if [event.get("sequence") for event in events] != list(
        range(1, expected_event_count + 1)
    ):
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)

    profile_interval = verify_profile_event(events[0], profile)
    check_intervals = verify_check_events(events[1:3], scoped_options)
    if min(interval[0] for interval in check_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result completed")

    if selected is not None:
        if created is None:
            fail("the expected reservation record is missing")
        reserve_interval = verify_reserve_event(events[3], selected, created)
        if reserve_interval[0] <= max(interval[1] for interval in check_intervals):
            fail("the reservation started before both availability checks completed")

    print(
        "verification passed: profile-derived date, concurrent availability checks, "
        "and the single permitted conditional reservation are valid"
    )


if __name__ == "__main__":
    main()
