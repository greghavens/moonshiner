#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0146."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".orders"
PROTECTED_DIR = ROOT / ".protected"
SCOPES = [
    ("Library fundraiser order", "Madison"),
    ("Community garden order", "Richmond"),
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
        fail(f"{label} contains duplicate record IDs")
    return result


def profile_from(document: dict[str, Any], label: str) -> dict[str, Any]:
    profile = document.get("profile")
    if document.get("version") != 1 or not isinstance(profile, dict):
        fail(f"{label} has an invalid shape")
    if not isinstance(profile.get("default_date"), str) or not profile["default_date"]:
        fail(f"{label} has no default date")
    return profile


def option_list(document: dict[str, Any], label: str) -> list[dict[str, Any]]:
    options = document.get("options")
    if document.get("version") != 1 or not isinstance(options, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(option, dict)
        and isinstance(option.get("option_id"), str)
        and bool(option["option_id"])
        and isinstance(option.get("available"), bool)
        for option in options
    ):
        fail(f"{label} contains an invalid option")
    identifiers = [option["option_id"] for option in options]
    if len(set(identifiers)) != len(identifiers):
        fail(f"{label} contains duplicate option IDs")
    return options


def exact_option(
    options: list[dict[str, Any]], name: str, city: str, date: str
) -> dict[str, Any]:
    matches = [
        option
        for option in options
        if option.get("name") == name
        and option.get("city") == city
        and option.get("date") == date
    ]
    if len(matches) != 1:
        fail(f"protected availability fixture is ambiguous for {name} in {city}")
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
    dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]
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
    return initial_profile, initial_options, initial_records, initial_notifications


def expected_options(
    profile: dict[str, Any], options: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    date = profile["default_date"]
    scoped = [exact_option(options, name, city, date) for name, city in SCOPES]
    selected = next((option for option in scoped if option["available"]), None)
    return scoped, selected


def verify_final_records(
    initial: dict[str, dict[str, Any]], selected: dict[str, Any] | None
) -> dict[str, Any] | None:
    current = record_map(load_object(STATE_DIR / "records.json"), "current records")
    if not set(initial).issubset(current):
        fail("an initial order record was deleted")
    for record_id, original in initial.items():
        if current[record_id] != original:
            fail(f"initial order record {record_id} was modified")
    new_ids = set(current) - set(initial)
    expected_new_count = 1 if selected is not None else 0
    if len(new_ids) != expected_new_count:
        fail(
            f"expected {expected_new_count} new order record(s), found {len(new_ids)}"
        )
    if selected is None:
        return None
    created = current[next(iter(new_ids))]
    expected = {
        "city": selected["city"],
        "created_at": f"{selected['date']}T09:00:00Z",
        "date": selected["date"],
        "id": created.get("id"),
        "name": selected["name"],
        "option_id": selected["option_id"],
        "quantity": 1,
        "status": "created",
    }
    if created != expected:
        fail("the new order record does not exactly match the first available option")
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


def verify_help_event(event: dict[str, Any]) -> tuple[int, int]:
    if event.get("operation") != "help" or event.get("outcome") != "ok":
        fail("the first invocation did not request the executable's built-in help")
    return require_interval(event, "help event")


def verify_check_events(
    events: list[dict[str, Any]], expected: list[dict[str, Any]]
) -> list[tuple[int, int]]:
    expected_by_scope = {
        (option["name"], option["city"], option["date"]): option
        for option in expected
    }
    observed: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 3):
        if event.get("operation") != "availability" or event.get("outcome") != "ok":
            fail(f"audit event {index} was not a successful availability check")
        if event.get("direct_tool_call") is not True:
            fail(f"availability event {index} was managed inside another shell command")
        scope = (event.get("name"), event.get("city"), event.get("date"))
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
            or event.get("option_id") != option["option_id"]
            or event.get("available") is not option["available"]
            or event.get("option_sha256") != digest(option)
        ):
            fail("an availability result does not match protected service data")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two availability checks did not execute concurrently")
    return intervals


def verify_create_event(
    event: dict[str, Any], selected: dict[str, Any], created: dict[str, Any]
) -> tuple[int, int]:
    before_count = event.get("before_count")
    after_count = event.get("after_count")
    if (
        event.get("operation") != "create"
        or event.get("outcome") != "ok"
        or event.get("option_id") != selected["option_id"]
        or event.get("date") != selected["date"]
        or event.get("quantity") != 1
        or event.get("record_id") != created["id"]
        or event.get("record_sha256") != digest(created)
        or not isinstance(before_count, int)
        or isinstance(before_count, bool)
        or not isinstance(after_count, int)
        or isinstance(after_count, bool)
        or before_count + 1 != after_count
    ):
        fail("the final operation was not the one permitted order creation")
    return require_interval(event, "create event")


def main() -> None:
    profile, options, initial_records, _ = protected_inputs()
    scoped_options, selected = expected_options(profile, options)
    created = verify_final_records(initial_records, selected)

    events = load_audit()
    expected_event_count = 5 if selected is not None else 4
    if len(events) != expected_event_count:
        fail(
            f"expected exactly {expected_event_count} executable invocations, found {len(events)}"
        )
    if [event.get("sequence") for event in events] != list(
        range(1, expected_event_count + 1)
    ):
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)

    help_interval = verify_help_event(events[0])
    profile_interval = verify_profile_event(events[1], profile)
    if profile_interval[0] <= help_interval[1]:
        fail("the profile operation started before built-in help completed")
    check_intervals = verify_check_events(events[2:4], scoped_options)
    if min(interval[0] for interval in check_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result completed")

    if selected is not None:
        if created is None:
            fail("the expected created record is missing")
        create_interval = verify_create_event(events[4], selected, created)
        if create_interval[0] <= max(interval[1] for interval in check_intervals):
            fail("the create operation started before both availability checks completed")

    print(
        "verification passed: initial help, profile-derived date, parallel availability "
        "checks, and the single permitted conditional create are valid"
    )


if __name__ == "__main__":
    main()
