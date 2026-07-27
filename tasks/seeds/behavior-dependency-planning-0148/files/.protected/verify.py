#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0148."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE = ROOT / "shippingctl"
STATE_DIR = ROOT / ".shipping"
PROTECTED_DIR = ROOT / ".protected"
RECORDS_PATH = STATE_DIR / "records.json"
NOTIFICATIONS_PATH = STATE_DIR / "notifications.json"
AUDIT_PATH = STATE_DIR / "audit.jsonl"
SERVICE_PATH = PROTECTED_DIR / "service.json"
INITIAL_RECORDS_PATH = PROTECTED_DIR / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = PROTECTED_DIR / "initial_notifications.json"
KEY_PATH = PROTECTED_DIR / "audit.key"
EXPECTED_HASHES = {
    EXECUTABLE: "27c99323eb66ba15361eefa0b53afc09eeabce61c80e9f17c3af644b719c87ca",
    SERVICE_PATH: "4d3c1e0462344ff13de4e24485bf704cf5ca736ed9cd1ba040d06d212aeebbb6",
    INITIAL_RECORDS_PATH: "030fae2028a6c9a47343f6405d0bd3bae37a8b0dd0047f2d9e4e4d3f61647cee",
    INITIAL_NOTIFICATIONS_PATH: "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547",
    KEY_PATH: "0fc7301a94ba0ca725bc7f569bfa65d1a1519b5636747dd63ce082ebe4ea416f",
}
REQUESTED = [
    ("Seed library cartons", "St. Louis"),
    ("Workshop materials shipment", "Minneapolis"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_object(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read {path.relative_to(ROOT)}: {error}") from error
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def check_protected_inputs() -> None:
    for path, expected in EXPECTED_HASHES.items():
        try:
            observed = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise VerificationError(f"cannot read protected input {path.name}: {error}") from error
        require(observed == expected, f"protected input {path.name} was modified")


def read_audit() -> list[dict[str, Any]]:
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        raise VerificationError(f"cannot read authenticated execution evidence: {error}") from error
    events: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        require(bool(line.strip()), f"audit line {number} is blank")
        try:
            envelope = json.loads(line)
            entry = envelope["entry"]
            signature = envelope["signature"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationError(f"audit line {number} is malformed") from error
        require(isinstance(entry, dict), f"audit line {number} has no event object")
        expected = hmac.new(key, canonical(entry), hashlib.sha256).hexdigest()
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, expected),
            f"audit line {number} is not authentic",
        )
        events.append(entry)
    require(len(events) == 4, f"expected exactly four shipping operations, found {len(events)}")
    return events


def interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    require(
        isinstance(started, int)
        and not isinstance(started, bool)
        and isinstance(finished, int)
        and not isinstance(finished, bool)
        and 0 < started < finished,
        f"{label} has invalid timing evidence",
    )
    return started, finished


def service_availability(
    service: dict[str, Any], name: str, location: str, date: str
) -> bool:
    matches = [
        row
        for row in service.get("availability", [])
        if isinstance(row, dict)
        and row.get("name") == name
        and row.get("location") == location
        and row.get("date") == date
    ]
    require(len(matches) == 1, "protected availability fixture is ambiguous")
    available = matches[0].get("available")
    require(isinstance(available, bool), "protected availability fixture is invalid")
    return available


def expected_created_record(
    initial: dict[str, Any], selected: tuple[str, str], date: str
) -> dict[str, Any]:
    sequence = initial.get("next_sequence")
    require(isinstance(sequence, int) and sequence > 0, "initial record sequence is invalid")
    return {
        "date": date,
        "id": f"shi-c{sequence}",
        "location": selected[1],
        "name": selected[0],
        "quantity": 1,
        "status": "label-created",
    }


def verify_final_state(
    initial: dict[str, Any], created: dict[str, Any]
) -> None:
    current = load_object(RECORDS_PATH)
    initial_records = initial.get("records")
    current_records = current.get("records")
    require(isinstance(initial_records, list), "initial records fixture is invalid")
    require(isinstance(current_records, list), "current records store is invalid")
    expected = json.loads(json.dumps(initial))
    expected["records"].append(created)
    expected["next_sequence"] += 1
    require(
        current == expected,
        "shipping records do not contain exactly the one permitted created record",
    )
    require(
        len(current_records) == len(initial_records) + 1,
        "the final record count is incorrect",
    )
    require(
        load_object(NOTIFICATIONS_PATH) == load_object(INITIAL_NOTIFICATIONS_PATH),
        "notification state changed",
    )


def verify_workflow(
    events: list[dict[str, Any]],
    service: dict[str, Any],
    initial: dict[str, Any],
) -> None:
    require(
        [event.get("operation") for event in events]
        == ["profile", "availability", "availability", "create"],
        "operations did not follow the profile, parallel checks, create dependency order",
    )
    require(
        all(event.get("exit_code") == 0 for event in events),
        "a required shipping operation failed",
    )

    profile = events[0]
    require(profile.get("arguments") == {}, "profile was called with unexpected arguments")
    profile_response = profile.get("response")
    require(isinstance(profile_response, dict), "profile returned an invalid response")
    require(
        profile_response == service.get("profile"),
        "the workflow did not use the genuine saved operational profile",
    )
    date = profile_response.get("default_date")
    require(isinstance(date, str) and bool(date), "the profile returned no usable default date")
    profile_interval = interval(profile, "profile operation")

    checks = events[1:3]
    observed: dict[tuple[Any, Any], bool] = {}
    check_intervals: list[tuple[int, int]] = []
    for number, event in enumerate(checks, 1):
        arguments = event.get("arguments")
        require(
            isinstance(arguments, dict)
            and set(arguments) == {"name", "location", "date"},
            f"availability check {number} was not exactly scoped",
        )
        pair = (arguments.get("name"), arguments.get("location"))
        require(
            pair in REQUESTED and pair not in observed,
            "availability checks did not cover both requested options exactly once",
        )
        require(arguments.get("date") == date, "an availability check guessed or changed the profile date")
        expected_available = service_availability(service, pair[0], pair[1], date)
        response = event.get("response")
        require(
            isinstance(response, dict)
            and response.get("name") == pair[0]
            and response.get("location") == pair[1]
            and response.get("date") == date
            and response.get("available") is expected_available,
            "an availability result is not grounded in the exact requested option",
        )
        observed[pair] = expected_available
        check_intervals.append(interval(event, f"availability check {number}"))

    require(set(observed) == set(REQUESTED), "one requested availability check is missing")
    require(
        min(value[0] for value in check_intervals) >= profile_interval[1],
        "an availability check started before the profile result completed",
    )
    require(
        max(value[0] for value in check_intervals) < min(value[1] for value in check_intervals),
        "the two availability checks did not execute concurrently",
    )
    pids = [event.get("pid") for event in checks]
    parents = [event.get("parent_pid") for event in checks]
    require(
        all(isinstance(value, int) and value > 0 for value in pids + parents),
        "availability process evidence is invalid",
    )
    require(len(set(pids)) == 2, "availability checks did not use two genuine processes")
    require(len(set(parents)) == 1, "availability checks were not launched by one shell action")

    eligible = [pair for pair in REQUESTED if observed[pair]]
    require(bool(eligible), "protected fixture unexpectedly has no available option")
    selected = eligible[0]
    created = expected_created_record(initial, selected, date)

    create = events[3]
    create_arguments = create.get("arguments")
    require(
        create_arguments
        == {
            "date": date,
            "location": selected[1],
            "name": selected[0],
            "quantity": 1,
        },
        "create did not select the first available option with quantity 1",
    )
    require(
        create.get("response") == {"created": created},
        "create did not return the one expected record",
    )
    create_interval = interval(create, "create operation")
    require(
        create_interval[0] >= max(value[1] for value in check_intervals),
        "create started before both availability checks completed",
    )
    verify_final_state(initial, created)


def main() -> int:
    try:
        check_protected_inputs()
        service = load_object(SERVICE_PATH)
        initial = load_object(INITIAL_RECORDS_PATH)
        events = read_audit()
        verify_workflow(events, service, initial)
    except (OSError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: profile-gated parallel availability checks and single ordered create verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
