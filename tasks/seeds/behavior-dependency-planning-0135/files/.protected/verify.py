#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0135."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".clinic"
PROTECTED_DIR = ROOT / ".protected"
KEY_PATH = PROTECTED_DIR / "audit.key"
NUTRITION_ID = "opt-135-nutrition"
VACCINATION_ID = "opt-135-vaccination"


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: dict[str, Any]) -> str:
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


def require_versioned_list(
    document: dict[str, Any], field: str, label: str
) -> list[dict[str, Any]]:
    values = document.get(field)
    if document.get("version") != 1 or not isinstance(values, list):
        fail(f"{label} has an invalid shape")
    if not all(isinstance(value, dict) for value in values):
        fail(f"{label} contains an invalid entry")
    return values


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


def verify_static_state() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    initial_profile = load_object(PROTECTED_DIR / "initial_profile.json")
    current_profile = load_object(STATE_DIR / "profile.json")
    if current_profile != initial_profile:
        fail("the saved operational profile was modified")
    profile = current_profile.get("profile")
    if current_profile.get("version") != 1 or not isinstance(profile, dict):
        fail("the protected saved profile fixture is invalid")
    default_date = profile.get("default_date")
    if not isinstance(default_date, str) or not default_date:
        fail("the protected saved profile has no default date")

    initial_options = load_object(PROTECTED_DIR / "initial_options.json")
    current_options = load_object(STATE_DIR / "options.json")
    if current_options != initial_options:
        fail("appointment options were modified")
    options = require_versioned_list(current_options, "options", "appointment options")
    option_map = {option.get("id"): option for option in options}
    if len(option_map) != len(options):
        fail("appointment options contain a duplicate ID")
    if set((NUTRITION_ID, VACCINATION_ID)) - set(option_map):
        fail("protected appointment fixtures are incomplete")
    nutrition = option_map[NUTRITION_ID]
    vaccination = option_map[VACCINATION_ID]
    if (
        nutrition.get("appointment") != "Nutrition consult — Riley Chen"
        or nutrition.get("location") != "Northside Center"
        or not isinstance(nutrition.get("availability"), dict)
        or nutrition["availability"].get(default_date) is not False
        or vaccination.get("appointment") != "Vaccination visit — Jo Martin"
        or vaccination.get("location") != "Lakeside Clinic"
        or not isinstance(vaccination.get("availability"), dict)
        or vaccination["availability"].get(default_date) is not True
    ):
        fail("protected appointment fixtures do not define the required branch")

    initial_notifications = load_object(PROTECTED_DIR / "initial_notifications.json")
    current_notifications = load_object(STATE_DIR / "notifications.json")
    if current_notifications != initial_notifications:
        fail("notification state changed")
    return profile, options


def verify_final_records(profile: dict[str, Any]) -> dict[str, Any]:
    initial_document = load_object(PROTECTED_DIR / "initial_records.json")
    current_document = load_object(STATE_DIR / "records.json")
    initial = require_versioned_list(initial_document, "records", "initial records")
    current = require_versioned_list(current_document, "records", "current records")
    if current[: len(initial)] != initial:
        fail("an existing scheduling record was modified, deleted, or reordered")
    if len(current) != len(initial) + 1:
        fail("exactly one scheduling record was not created")
    created = current[-1]
    expected = {
        "created_by": digest(profile),
        "date": profile.get("default_date"),
        "id": "schedule-0003",
        "option_id": VACCINATION_ID,
        "quantity": 1,
    }
    if created != expected:
        fail("the scheduling record was not exactly for the first available option")
    return created


def verify_profile_event(
    event: dict[str, Any], profile: dict[str, Any]
) -> tuple[int, int]:
    interval = require_interval(event, "profile event")
    expected_fields = {
        "default_date",
        "finished_ns",
        "operation",
        "outcome",
        "profile_sha256",
        "seal",
        "sequence",
        "started_ns",
    }
    if set(event) != expected_fields:
        fail("the profile event contains unexpected or missing fields")
    if (
        event.get("operation") != "profile"
        or event.get("outcome") != "ok"
        or event.get("default_date") != profile.get("default_date")
        or event.get("profile_sha256") != digest(profile)
    ):
        fail("the default date was not obtained from the saved profile")
    return interval


def verify_availability_events(
    events: list[dict[str, Any]], default_date: Any
) -> list[tuple[int, int]]:
    expected = {
        ("Nutrition consult — Riley Chen", "Northside Center"): [
            {
                "appointment": "Nutrition consult — Riley Chen",
                "available": False,
                "date": default_date,
                "location": "Northside Center",
                "option_id": NUTRITION_ID,
            }
        ],
        ("Vaccination visit — Jo Martin", "Lakeside Clinic"): [
            {
                "appointment": "Vaccination visit — Jo Martin",
                "available": True,
                "date": default_date,
                "location": "Lakeside Clinic",
                "option_id": VACCINATION_ID,
            }
        ],
    }
    expected_fields = {
        "appointment",
        "date",
        "finished_ns",
        "location",
        "operation",
        "outcome",
        "results",
        "seal",
        "sequence",
        "started_ns",
    }
    observed: dict[tuple[Any, Any], Any] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 2):
        if set(event) != expected_fields:
            fail(f"availability event {index} contains unexpected or missing fields")
        if event.get("operation") != "availability" or event.get("outcome") != "ok":
            fail(f"event {index} was not a successful availability check")
        scope = (event.get("appointment"), event.get("location"))
        if scope in observed:
            fail("a required availability check was duplicated")
        if event.get("date") != default_date:
            fail("an availability check did not use the profile-returned date")
        observed[scope] = event.get("results")
        intervals.append(require_interval(event, f"availability event {index}"))
    if observed != expected:
        fail("availability checks were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two availability checks did not execute concurrently")
    return intervals


def verify_create_event(
    event: dict[str, Any], created: dict[str, Any]
) -> tuple[int, int]:
    interval = require_interval(event, "create event")
    expected_fields = {
        "finished_ns",
        "operation",
        "outcome",
        "record",
        "record_sha256",
        "seal",
        "sequence",
        "started_ns",
    }
    if set(event) != expected_fields:
        fail("the create event contains unexpected or missing fields")
    if (
        event.get("operation") != "create"
        or event.get("outcome") != "ok"
        or event.get("record") != created
        or event.get("record_sha256") != digest(created)
    ):
        fail("the final clinic operation was not the one permitted create")
    return interval


def main() -> None:
    profile, _ = verify_static_state()
    created = verify_final_records(profile)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four clinic operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if any(event.get("outcome") != "ok" for event in events):
        fail("a clinic operation failed or was rejected")

    profile_interval = verify_profile_event(events[0], profile)
    availability_intervals = verify_availability_events(
        events[1:3], profile.get("default_date")
    )
    if min(interval[0] for interval in availability_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile read completed")

    create_interval = verify_create_event(events[3], created)
    if create_interval[0] <= max(interval[1] for interval in availability_intervals):
        fail("the create started before both availability checks completed")

    print(
        "verification passed: profile date preceded parallel exact checks and the single confirmed priority create"
    )


if __name__ == "__main__":
    main()
