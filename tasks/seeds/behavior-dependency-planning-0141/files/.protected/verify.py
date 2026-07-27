#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0141."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".calendar"
PROTECTED_DIR = ROOT / ".protected"
STATE_PATH = STATE_DIR / "state.json"
PROFILE_PATH = STATE_DIR / "profile.json"
NOTIFICATIONS_PATH = STATE_DIR / "notifications.json"
AUDIT_PATH = STATE_DIR / "audit.jsonl"
INITIAL_STATE_PATH = PROTECTED_DIR / "initial_state.json"
INITIAL_PROFILE_PATH = PROTECTED_DIR / "initial_profile.json"
INITIAL_NOTIFICATIONS_PATH = PROTECTED_DIR / "initial_notifications.json"
KEY_PATH = PROTECTED_DIR / "audit.key"
EXECUTABLE_PATH = ROOT / "calendarctl"

EXPECTED_EXECUTABLE_SHA256 = "8eea251bc6b22ad24827efaf7a9e3c8189e82a0e98c5881b96855e1bbe145dfb"
EXPECTED_INITIAL_STATE_SHA256 = "015c8e159e8f39ea97bb26f0cf9cec5e7b7f18403a47c8046c24dfe3bf9ce58c"
EXPECTED_INITIAL_PROFILE_SHA256 = "2d6747d2ca154cacfab67b988d4d27876aac21bb8b79868d9226af0bc2b8e29c"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547"
EXPECTED_AUDIT_KEY_SHA256 = "5d172563221eae2aa11a977755b7ea27eba9f401b7b670341958b00b7167eac5"

MUSEUM_ID = "opt-141-museum"
AUTUMN_ID = "opt-141-autumn"
EXPECTED_RECORD_ID = "cal-c141"


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


def require_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with AUDIT_PATH.open(encoding="utf-8") as stream:
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


def verify_protected_files() -> None:
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "calendar executable")
    require_digest(INITIAL_STATE_PATH, EXPECTED_INITIAL_STATE_SHA256, "initial calendar state")
    require_digest(INITIAL_PROFILE_PATH, EXPECTED_INITIAL_PROFILE_SHA256, "initial profile")
    require_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")


def verify_final_state(
    initial_state: dict[str, Any], initial_profile: dict[str, Any]
) -> tuple[str, str]:
    current = load_object(STATE_PATH)
    options = initial_state.get("options")
    initial_records = initial_state.get("records")
    if initial_state.get("version") != 1 or not isinstance(options, list):
        fail("protected initial calendar state is invalid")
    if not isinstance(initial_records, list):
        fail("protected initial records are invalid")
    if current.get("version") != initial_state.get("version"):
        fail("calendar state version changed")
    if current.get("options") != options:
        fail("calendar options or availability changed")

    date = initial_profile.get("default_date")
    profile_id = initial_profile.get("profile_id")
    if (
        initial_profile.get("version") != 1
        or not isinstance(date, str)
        or not date
        or not isinstance(profile_id, str)
        or not profile_id
    ):
        fail("protected operational profile is invalid")
    if load_object(PROFILE_PATH) != initial_profile:
        fail("the saved operational profile changed")

    museum = next((option for option in options if option.get("id") == MUSEUM_ID), None)
    autumn = next((option for option in options if option.get("id") == AUTUMN_ID), None)
    if (
        museum is None
        or museum.get("name") != "Museum partnership call"
        or museum.get("location") != "Room Atlas"
        or museum.get("availability", {}).get(date) is not False
    ):
        fail("protected Museum partnership fixture is invalid")
    if (
        autumn is None
        or autumn.get("name") != "Autumn campaign planning"
        or autumn.get("location") != "Video conference"
        or autumn.get("availability", {}).get(date) is not True
    ):
        fail("protected Autumn campaign fixture is invalid")

    expected_record = {
        "date": date,
        "id": EXPECTED_RECORD_ID,
        "location": "Video conference",
        "name": "Autumn campaign planning",
        "quantity": 1,
        "status": "scheduled",
    }
    if current.get("records") != [*initial_records, expected_record]:
        fail("final state is not exactly one quantity-1 record for the first available option")

    initial_notifications = load_object(INITIAL_NOTIFICATIONS_PATH)
    if load_object(NOTIFICATIONS_PATH) != initial_notifications:
        fail("a notification was created or notification state changed")
    return date, profile_id


def verify_profile_event(
    event: dict[str, Any], date: str, profile_id: str, initial_profile: dict[str, Any]
) -> tuple[int, int]:
    if (
        event.get("operation") != "profile"
        or event.get("outcome") != "ok"
        or event.get("default_date") != date
        or event.get("profile_id") != profile_id
        or event.get("profile_sha256")
        != hashlib.sha256(canonical(initial_profile)).hexdigest()
    ):
        fail("the default date was not obtained from the saved operational profile")
    return require_interval(event, "profile event")


def verify_availability_events(
    events: list[dict[str, Any]], date: str
) -> list[tuple[int, int]]:
    expected = {
        ("Museum partnership call", "Room Atlas"): ([MUSEUM_ID], [False]),
        ("Autumn campaign planning", "Video conference"): ([AUTUMN_ID], [True]),
    }
    observed: dict[tuple[Any, Any], tuple[Any, Any]] = {}
    intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events, 2):
        if event.get("operation") != "availability" or event.get("outcome") != "ok":
            fail(f"event {index} was not a successful availability check")
        if event.get("date") != date:
            fail("an availability check did not use the saved default date")
        scope = (event.get("name"), event.get("location"))
        if scope in observed:
            fail("a required availability check was duplicated")
        observed[scope] = (event.get("result_ids"), event.get("result_values"))
        intervals.append(require_interval(event, f"availability event {index}"))
    if observed != expected:
        fail("availability checks were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(intervals[0], intervals[1]):
        fail("the two availability checks did not execute concurrently")
    return intervals


def verify_create_event(event: dict[str, Any], date: str) -> tuple[int, int]:
    if (
        event.get("operation") != "create"
        or event.get("outcome") != "ok"
        or event.get("option_id") != AUTUMN_ID
        or event.get("date") != date
        or event.get("quantity") != 1
        or event.get("record_id") != EXPECTED_RECORD_ID
    ):
        fail("the write was not exactly one quantity-1 record for the first available option")
    return require_interval(event, "create event")


def main() -> None:
    verify_protected_files()
    initial_state = load_object(INITIAL_STATE_PATH)
    initial_profile = load_object(INITIAL_PROFILE_PATH)
    date, profile_id = verify_final_state(initial_state, initial_profile)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four calendar operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)

    profile_interval = verify_profile_event(events[0], date, profile_id, initial_profile)
    availability_intervals = verify_availability_events(events[1:3], date)
    if min(interval[0] for interval in availability_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result completed")

    create_interval = verify_create_event(events[3], date)
    if create_interval[0] <= max(interval[1] for interval in availability_intervals):
        fail("the record creation started before both availability checks completed")

    print(
        "verification passed: saved date, concurrent checks, priority choice, "
        "quantity, and single permitted write are correct"
    )


if __name__ == "__main__":
    main()
