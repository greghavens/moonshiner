#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0144."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".messaging"
PROTECTED_DIR = ROOT / ".protected"
KEY_PATH = PROTECTED_DIR / "audit.key"
NEW_RECORD_ID = "mes-c144"
TARGETS = (
    ("Scholarship deadline reminder", "All Staff"),
    ("Garden workday invitation", "Community Partners"),
)


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
        fail(f"{label} contains a duplicate record ID")
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


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    process_id = event.get("pid")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
        or not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
    ):
        fail(f"{label} has invalid executable-process evidence")
    return started, finished


def active_option(
    availability: dict[str, Any], name: str, channel: str, date: str
) -> dict[str, Any]:
    options = availability.get("options")
    if availability.get("version") != 1 or not isinstance(options, list):
        fail("protected availability fixture has an invalid shape")
    matches = [
        option
        for option in options
        if isinstance(option, dict)
        and option.get("name") == name
        and option.get("channel") == channel
        and option.get("date") == date
        and option.get("archived") is False
    ]
    if len(matches) != 1:
        fail("a required availability scope is not unique in the protected fixture")
    return matches[0]


def verify_immutable_service_data() -> tuple[dict[str, Any], dict[str, Any]]:
    initial_profile = load_object(PROTECTED_DIR / "initial_profile.json")
    current_profile = load_object(STATE_DIR / "profile.json")
    if current_profile != initial_profile:
        fail("the saved operational profile changed")

    initial_availability = load_object(PROTECTED_DIR / "initial_availability.json")
    current_availability = load_object(STATE_DIR / "availability.json")
    if current_availability != initial_availability:
        fail("availability or a related/archived option changed")

    initial_notifications = load_object(PROTECTED_DIR / "initial_notifications.json")
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")
    return initial_profile, initial_availability


def verify_final_records(
    expected_choice: tuple[str, str], default_date: str
) -> dict[str, Any]:
    initial_document = load_object(PROTECTED_DIR / "initial_records.json")
    current_document = load_object(STATE_DIR / "records.json")
    initial = record_map(initial_document, "protected initial records")
    current = record_map(current_document, "current records")
    if set(current) != set(initial) | {NEW_RECORD_ID}:
        fail("the workflow did not create exactly one new record")
    for record_id, original in initial.items():
        if current.get(record_id) != original:
            fail(f"existing, related, or archived record {record_id} changed")
    name, channel = expected_choice
    expected = {
        "archived": False,
        "channel": channel,
        "date": default_date,
        "id": NEW_RECORD_ID,
        "name": name,
        "quantity": 1,
        "status": "draft",
    }
    if current.get(NEW_RECORD_ID) != expected:
        fail("the created record is not exactly the first available requested option")
    return expected


def main() -> None:
    profile, availability = verify_immutable_service_data()
    default_date = profile.get("default_date")
    if not isinstance(default_date, str) or not default_date:
        fail("protected profile has no default date")

    required_options = {
        (name, channel): active_option(availability, name, channel, default_date)
        for name, channel in TARGETS
    }
    available_targets = [
        scope
        for scope in TARGETS
        if required_options[scope].get("available") is True
    ]
    if not available_targets:
        fail("protected branch does not exercise the required create path")
    expected_choice = available_targets[0]
    expected_record = verify_final_records(expected_choice, default_date)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four messaging operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events] != [
        "profile",
        "availability",
        "availability",
        "create",
    ]:
        fail("operations did not follow profile, parallel checks, then one create")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a messaging operation failed")

    profile_event = events[0]
    profile_interval = interval(profile_event, "profile event")
    public_profile = {
        "default_date": default_date,
        "preferred_quantity": profile.get("preferred_quantity"),
        "timezone": profile.get("timezone"),
    }
    if (
        profile_event.get("default_date") != default_date
        or profile_event.get("result_sha256") != digest(public_profile)
    ):
        fail("the first operation did not return the saved operational profile")

    observed_checks: dict[tuple[Any, Any], dict[str, Any]] = {}
    check_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[1:3], 2):
        scope = (event.get("name"), event.get("channel"))
        if scope in observed_checks:
            fail("a required availability check was duplicated")
        option = required_options.get(scope)
        if option is None or event.get("date") != default_date:
            fail("an availability check was broad, unrelated, or used the wrong date")
        public_result = {
            "available": option.get("available") is True,
            "channel": scope[1],
            "date": default_date,
            "name": scope[0],
        }
        if (
            event.get("available") is not public_result["available"]
            or event.get("result_sha256") != digest(public_result)
        ):
            fail("an availability result does not match the executable response")
        observed_checks[scope] = event
        check_intervals.append(interval(event, f"availability event {index}"))
    if set(observed_checks) != set(TARGETS):
        fail("the two exact requested availability checks were not both completed")
    if min(start for start, _ in check_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result returned")
    if max(start for start, _ in check_intervals) >= min(
        finish for _, finish in check_intervals
    ):
        fail("the two availability checks did not execute concurrently")

    create = events[3]
    create_interval = interval(create, "create event")
    if create_interval[0] <= max(finish for _, finish in check_intervals):
        fail("the create began before both availability results returned")
    expected_name, expected_channel = expected_choice
    if (
        create.get("name") != expected_name
        or create.get("channel") != expected_channel
        or create.get("date") != default_date
        or create.get("quantity") != 1
        or create.get("record_id") != NEW_RECORD_ID
        or create.get("record_sha256") != digest(expected_record)
    ):
        fail("the single create was not for the first available option with quantity 1")

    print(
        "verification passed: profile-gated parallel availability checks preceded exactly one scoped create"
    )


if __name__ == "__main__":
    main()
