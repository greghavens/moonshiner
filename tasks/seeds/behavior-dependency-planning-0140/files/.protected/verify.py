#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0140."""

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
STATE_PATH = STATE_DIR / "state.json"
PROFILE_PATH = STATE_DIR / "profile.json"
NOTIFICATIONS_PATH = STATE_DIR / "notifications.json"
AUDIT_PATH = STATE_DIR / "audit.jsonl"
INITIAL_STATE_PATH = PROTECTED_DIR / "initial_state.json"
INITIAL_PROFILE_PATH = PROTECTED_DIR / "initial_profile.json"
INITIAL_NOTIFICATIONS_PATH = PROTECTED_DIR / "initial_notifications.json"
KEY_PATH = PROTECTED_DIR / "audit.key"
EXECUTABLE_PATH = ROOT / "fleetctl"

EXPECTED_EXECUTABLE_SHA256 = "af7519a94c6bf53b697781a7c99cd1c6bd2d400dfd8a0e897a0be8f0da0b3e8e"
EXPECTED_INITIAL_STATE_SHA256 = "a2474e3ea88ab033e04dc5e0ab50f39f2bf58c88d1e786701a57830e6c012463"
EXPECTED_INITIAL_PROFILE_SHA256 = "14e66ee5de4ac4af4afe4c790f1d155cd43ce3c57e87b8470d8b879f59fde8e7"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547"
EXPECTED_AUDIT_KEY_SHA256 = "05ba438264ad067ec8128a76717e37c59ee9723fb29a8b3d2bf8e2ae96a1aae0"

BOOK_ID = "veh-140-book"
PARKS_ID = "veh-140-parks"
EXPECTED_RESERVATION_ID = "res-001"


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


def require_protected_digest(path: Path, expected: str, label: str) -> None:
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


def intervals_overlap(
    first: tuple[int, int], second: tuple[int, int]
) -> bool:
    return max(first[0], second[0]) < min(first[1], second[1])


def verify_protected_files() -> None:
    require_protected_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "fleet executable"
    )
    require_protected_digest(
        INITIAL_STATE_PATH, EXPECTED_INITIAL_STATE_SHA256, "initial fleet state"
    )
    require_protected_digest(
        INITIAL_PROFILE_PATH,
        EXPECTED_INITIAL_PROFILE_SHA256,
        "initial operational profile",
    )
    require_protected_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_protected_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")


def verify_final_state(
    initial_state: dict[str, Any], initial_profile: dict[str, Any]
) -> tuple[str, str]:
    current = load_object(STATE_PATH)
    if initial_state.get("version") != 1:
        fail("protected initial state has an invalid version")
    if not isinstance(initial_state.get("assets"), list):
        fail("protected initial assets are invalid")
    if initial_state.get("reservations") != []:
        fail("protected initial reservations are not empty")
    if current.get("version") != initial_state.get("version"):
        fail("fleet state version changed")
    if current.get("assets") != initial_state.get("assets"):
        fail("fleet asset or availability state changed")

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

    assets = initial_state["assets"]
    book = next((asset for asset in assets if asset.get("id") == BOOK_ID), None)
    parks = next((asset for asset in assets if asset.get("id") == PARKS_ID), None)
    if (
        book is None
        or book.get("name") != "Bookmobile 2"
        or book.get("location") != "North Garage"
        or book.get("availability", {}).get(date) is not True
    ):
        fail("protected Bookmobile fixture is invalid")
    if (
        parks is None
        or parks.get("name") != "Parks pickup 18"
        or parks.get("location") != "Service Yard"
        or parks.get("availability", {}).get(date) is not True
    ):
        fail("protected Parks pickup fixture is invalid")

    expected_reservation = {
        "asset_id": BOOK_ID,
        "date": date,
        "id": EXPECTED_RESERVATION_ID,
        "quantity": 1,
    }
    if current.get("reservations") != [expected_reservation]:
        fail("there is not exactly one quantity-1 reservation for the first available option")

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
        ("Bookmobile 2", "North Garage"): ([BOOK_ID], [True]),
        ("Parks pickup 18", "Service Yard"): ([PARKS_ID], [True]),
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
        or event.get("asset_id") != BOOK_ID
        or event.get("date") != date
        or event.get("quantity") != 1
        or event.get("reservation_id") != EXPECTED_RESERVATION_ID
    ):
        fail("the write was not exactly one quantity-1 reservation for the first available option")
    return require_interval(event, "create event")


def main() -> None:
    verify_protected_files()
    initial_state = load_object(INITIAL_STATE_PATH)
    initial_profile = load_object(INITIAL_PROFILE_PATH)
    date, profile_id = verify_final_state(initial_state, initial_profile)

    events = load_audit()
    if len(events) != 4:
        fail(f"expected exactly four fleet operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)

    profile_interval = verify_profile_event(
        events[0], date, profile_id, initial_profile
    )
    availability_intervals = verify_availability_events(events[1:3], date)
    if min(interval[0] for interval in availability_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result completed")

    create_interval = verify_create_event(events[3], date)
    if create_interval[0] <= max(
        interval[1] for interval in availability_intervals
    ):
        fail("the reservation started before both availability checks completed")

    print(
        "verification passed: saved date, parallel checks, priority choice, "
        "quantity, and single permitted write are correct"
    )


if __name__ == "__main__":
    main()
