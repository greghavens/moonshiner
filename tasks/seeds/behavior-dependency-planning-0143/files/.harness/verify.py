#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-dependency-planning-0143."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".hospitality"
EXECUTABLE_PATH = ROOT / "hospitalityctl"
KEY_PATH = ROOT / ".harness" / "audit.key"
INITIAL_PROFILE_PATH = ROOT / ".harness" / "initial_profile.json"
INITIAL_AVAILABILITY_PATH = ROOT / ".harness" / "initial_availability.json"
INITIAL_RECORDS_PATH = ROOT / ".harness" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".harness" / "initial_notifications.json"

EXPECTED_EXECUTABLE_SHA256 = "da4b6c6d5db9b21f749ac2e156ec4c11d99cd8816638d176424c18c5dc77dfb2"
EXPECTED_KEY_SHA256 = "4a904418424c983f826ad14104e72fce6e77e46c0addb5135120a653f5c02bc9"
EXPECTED_PROFILE_SHA256 = "14dab6c3dfeb12b36a2f2f0371e8912402d6e2f2e789227f63af02583f1a7de2"
EXPECTED_AVAILABILITY_SHA256 = "ea5887c7a6cde2051d50d28ad410ef834ba9c18cf1f3616e94782dd51518e5ac"
EXPECTED_RECORDS_SHA256 = "045a397a5473dfb6aacf129cccf09c1a850cb3c89b5e5da3a155c542305561b4"
EXPECTED_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"

RIVER = ("River Room reunion", "Portland")
JUNIPER = ("Juniper Suite stay", "Savannah")
CREATED_ID = "hos-c143"


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
    mapped = {record["id"]: record for record in records}
    if len(mapped) != len(records):
        fail(f"{label} contains duplicate record IDs")
    return mapped


def check_workspace_scope() -> None:
    tracked_baseline = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "README.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    # Authoring workspaces hold the fixture one directory below their Git root.
    # Materialized evaluation workspaces always track README.md at ROOT.
    if tracked_baseline.returncode != 0:
        return
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "."],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed: set[str] = set()
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.add(path)
    allowed = {".hospitality/audit.jsonl", ".hospitality/records.json"}
    unexpected = sorted(changed - allowed)
    if unexpected:
        fail("unexpected lasting workspace changes: " + ", ".join(unexpected))


def check_protected_material() -> None:
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "hospitalityctl")
    require_digest(KEY_PATH, EXPECTED_KEY_SHA256, "audit key")
    require_digest(INITIAL_PROFILE_PATH, EXPECTED_PROFILE_SHA256, "initial profile")
    require_digest(
        INITIAL_AVAILABILITY_PATH,
        EXPECTED_AVAILABILITY_SHA256,
        "initial availability",
    )
    require_digest(INITIAL_RECORDS_PATH, EXPECTED_RECORDS_SHA256, "initial records")
    require_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_NOTIFICATIONS_SHA256,
        "initial notifications",
    )


def check_state(
    profile: dict[str, Any],
    availability: dict[str, Any],
    initial_records_document: dict[str, Any],
) -> None:
    if load_object(STATE_DIR / "profile.json") != profile:
        fail("saved profile state changed")
    if load_object(STATE_DIR / "availability.json") != availability:
        fail("availability state changed")
    if load_object(STATE_DIR / "notifications.json") != load_object(
        INITIAL_NOTIFICATIONS_PATH
    ):
        fail("notification state changed")

    current_document = load_object(STATE_DIR / "records.json")
    initial = record_map(initial_records_document, "protected initial records")
    current = record_map(current_document, "current records")
    if set(current) != set(initial) | {CREATED_ID}:
        fail("the record set does not contain exactly one new record")
    for record_id, original in initial.items():
        if current.get(record_id) != original:
            fail(f"pre-existing record {record_id} was modified")

    default_date = profile.get("default_date")
    expected_created = {
        "id": CREATED_ID,
        "name": JUNIPER[0],
        "location": JUNIPER[1],
        "date": default_date,
        "quantity": 1,
        "status": "confirmed",
    }
    if current.get(CREATED_ID) != expected_created:
        fail("the created record is not exactly the first available requested option")
    initial_next = initial_records_document.get("next_number")
    if not isinstance(initial_next, int) or isinstance(initial_next, bool):
        fail("protected initial record sequence is invalid")
    if current_document.get("next_number") != initial_next + 1:
        fail("record sequence does not show exactly one creation")


def check_audit(
    events: list[dict[str, Any]],
    profile: dict[str, Any],
) -> None:
    if len(events) != 4:
        fail(f"expected exactly four registry operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4]:
        fail("audit sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events] != [
        "profile",
        "availability",
        "availability",
        "create",
    ]:
        fail("registry operations did not follow the required dependency layers")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a registry operation failed or was rejected")

    default_date = profile.get("default_date")
    profile_event = events[0]
    profile_interval = interval(profile_event, "profile event")
    if profile_event.get("result_default_date") != default_date:
        fail("the profile lookup did not return the protected default date")
    if profile_event.get("result_profile_sha256") != hashlib.sha256(
        canonical(profile)
    ).hexdigest():
        fail("the profile result was not the complete saved profile")

    expected_availability = {
        (RIVER[0], RIVER[1], default_date): False,
        (JUNIPER[0], JUNIPER[1], default_date): True,
    }
    actual_availability: dict[tuple[Any, Any, Any], Any] = {}
    availability_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[1:3], 2):
        scope = (event.get("name"), event.get("location"), event.get("date"))
        if scope in actual_availability:
            fail("a required availability check was duplicated")
        actual_availability[scope] = event.get("result_available")
        availability_intervals.append(interval(event, f"availability event {index}"))
    if actual_availability != expected_availability:
        fail("availability checks used the wrong option, location, date, or result")
    if not overlap(availability_intervals[0], availability_intervals[1]):
        fail("the two availability checks did not execute concurrently")
    if min(item[0] for item in availability_intervals) <= profile_interval[1]:
        fail("an availability check started before the profile result completed")

    create = events[3]
    create_interval = interval(create, "create event")
    if create_interval[0] <= max(item[1] for item in availability_intervals):
        fail("creation started before both availability checks completed")
    expected_create_fields = {
        "record_id": CREATED_ID,
        "name": JUNIPER[0],
        "location": JUNIPER[1],
        "date": default_date,
        "quantity": 1,
    }
    for field, expected in expected_create_fields.items():
        if create.get(field) != expected:
            fail(f"create operation has the wrong {field}")


def main() -> None:
    check_protected_material()
    profile = load_object(INITIAL_PROFILE_PATH)
    availability = load_object(INITIAL_AVAILABILITY_PATH)
    initial_records = load_object(INITIAL_RECORDS_PATH)
    check_state(profile, availability, initial_records)
    check_audit(load_audit(), profile)
    check_workspace_scope()
    print(
        "verification passed: saved profile, parallel availability checks, and only the confirmed first-available creation"
    )


if __name__ == "__main__":
    main()
