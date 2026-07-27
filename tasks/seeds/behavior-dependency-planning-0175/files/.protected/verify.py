#!/usr/bin/env python3
"""Protected deterministic verifier for behavior-dependency-planning-0175."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".clinic"
INITIAL_APPOINTMENTS_PATH = ROOT / ".protected" / "initial_appointments.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "clinicctl"
EXPECTED_EXECUTABLE_SHA256 = "58d45b70541c9ac82bea64b5cd1163b781695eccbc45683a228972409bb8d666"
EXPECTED_INITIAL_APPOINTMENTS_SHA256 = "a0bd360778b4d3d338fa260af46b5bd23aa34b721c5e1d77e32ce5336fd1c2e4"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "152e64b023af00db83befd18aea91d7976399d7c3bc5f091c97e0814b1e7d73f"
EXPECTED_KEY_SHA256 = "7454cd9147ead85c53b19a973ee15e80db45f5b96629a6226926920065c7bb1a"
HEARING_KEY = ("Hearing test", "Taylor Singh", "Cedar Clinic")
DERMATOLOGY_KEY = ("Dermatology follow-up", "Robin Park", "Dale Clinic")
CANCELLATION_REASON = "Patient requested a replacement appointment"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_document(path: Path, collection: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"cannot read {path.relative_to(ROOT)}: {error}")
    require(
        isinstance(value, dict)
        and value.get("version") == 1
        and isinstance(value.get(collection), list)
        and all(isinstance(item, dict) for item in value[collection]),
        f"{path.relative_to(ROOT)} has an invalid shape",
    )
    return value


def appointment_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    records = document["appointments"]
    require(
        all(isinstance(record.get("id"), str) and record["id"] for record in records),
        f"{label} contains an invalid appointment",
    )
    result = {record["id"]: record for record in records}
    require(len(result) == len(records), f"{label} contains a duplicate ID")
    return result


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


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
                    raise VerificationError(
                        f"audit line {line_number} is invalid JSON: {error}"
                    )
                require(
                    isinstance(event, dict),
                    f"audit line {line_number} is not an object",
                )
                events.append(event)
    except OSError as error:
        raise VerificationError(f"cannot read operation history: {error}")
    return events


def verify_protected_inputs() -> None:
    require(
        sha256(EXECUTABLE_PATH) == EXPECTED_EXECUTABLE_SHA256,
        "clinicctl was modified",
    )
    require(
        sha256(INITIAL_APPOINTMENTS_PATH) == EXPECTED_INITIAL_APPOINTMENTS_SHA256,
        "protected initial appointments were modified",
    )
    require(
        sha256(INITIAL_NOTIFICATIONS_PATH)
        == EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "protected initial notifications were modified",
    )
    require(sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")


def verify_seals(events: list[dict[str, Any]]) -> None:
    key = KEY_PATH.read_bytes().strip()
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        require(isinstance(seal, str) and bool(seal), f"event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(seal, expected),
            f"event {index} has an invalid seal",
        )


def require_interval(event: dict[str, Any], label: str) -> tuple[int, int]:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    require(
        isinstance(started, int)
        and not isinstance(started, bool)
        and isinstance(finished, int)
        and not isinstance(finished, bool)
        and started < finished,
        f"{label} has an invalid execution interval",
    )
    return started, finished


def verify_parallel_phase(events: list[dict[str, Any]], label: str) -> None:
    require(len(events) == 2, f"{label} phase must contain exactly two operations")
    require(
        len({event.get("process_id") for event in events}) == 2,
        f"{label} branches were not separate executable processes",
    )
    require(
        len({event.get("parent_process_id") for event in events}) == 1,
        f"{label} branches were not launched together",
    )
    intervals = [require_interval(event, label) for event in events]
    require(
        max(interval[0] for interval in intervals)
        < min(interval[1] for interval in intervals),
        f"{label} processes did not overlap",
    )


def find_unique(
    appointments: dict[str, dict[str, Any]], key: tuple[str, str, str]
) -> dict[str, Any]:
    matches = [
        record
        for record in appointments.values()
        if (
            record.get("appointment"),
            record.get("patient"),
            record.get("location"),
        )
        == key
    ]
    require(len(matches) == 1, f"protected lookup is not unique: {key!r}")
    return matches[0]


def verify_execution(
    events: list[dict[str, Any]],
    hearing: dict[str, Any],
    dermatology: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    require(len(events) == 6, f"expected exactly six clinic operations, found {len(events)}")
    require(
        [event.get("sequence") for event in events] == [1, 2, 3, 4, 5, 6],
        "operation sequence is incomplete or reordered",
    )
    require(
        [event.get("operation") for event in events]
        == ["search", "search", "get", "get", "cancel", "notify"],
        "operations must be two searches, two gets, one cancellation, then one notice",
    )
    require(
        all(event.get("outcome") == "ok" for event in events),
        "a clinic operation failed or an extra failure was recorded",
    )
    verify_seals(events)

    searches = events[:2]
    gets = events[2:4]
    cancellation = events[4]
    notice = events[5]
    expected_by_key = {HEARING_KEY: hearing, DERMATOLOGY_KEY: dermatology}
    actual_searches: dict[tuple[Any, Any, Any], Any] = {}
    for event in searches:
        key = (
            event.get("appointment"),
            event.get("patient"),
            event.get("location"),
        )
        require(key not in actual_searches, "a requested search was duplicated")
        actual_searches[key] = event.get("result_ids")
    require(
        actual_searches
        == {key: [record["id"]] for key, record in expected_by_key.items()},
        "searches were broad, incorrect, ambiguous, or incomplete",
    )
    verify_parallel_phase(searches, "search")

    expected_get_hashes = {
        hearing["id"]: record_digest(hearing),
        dermatology["id"]: record_digest(dermatology),
    }
    actual_get_hashes: dict[Any, Any] = {}
    for event in gets:
        require(event.get("found") is True, "a complete-record retrieval failed")
        record_id = event.get("record_id")
        require(record_id not in actual_get_hashes, "a requested get was duplicated")
        actual_get_hashes[record_id] = event.get("record_sha256")
    require(
        actual_get_hashes == expected_get_hashes,
        "retrievals did not return both exact complete records",
    )
    verify_parallel_phase(gets, "retrieval")
    search_intervals = [require_interval(event, "search") for event in searches]
    get_intervals = [require_interval(event, "retrieval") for event in gets]
    require(
        min(interval[0] for interval in get_intervals)
        > max(interval[1] for interval in search_intervals),
        "a retrieval started before both searches completed",
    )

    cancellation_interval = require_interval(cancellation, "cancellation")
    require(
        cancellation_interval[0] > max(interval[1] for interval in get_intervals),
        "cancellation started before both complete records were retrieved",
    )
    require(
        cancellation.get("record_id") == dermatology["id"]
        and cancellation.get("conditional_status") == "requested"
        and cancellation.get("before_status") == "requested"
        and cancellation.get("after_status") == "cancelled"
        and cancellation.get("reason") == CANCELLATION_REASON
        and cancellation.get("cancelled") is True
        and isinstance(cancellation.get("receipt"), str)
        and bool(cancellation["receipt"]),
        "the sole mutation was not the permitted guarded cancellation",
    )

    notice_interval = require_interval(notice, "notification")
    require(
        notice_interval[0] > cancellation_interval[1],
        "appointment desk was notified before cancellation succeeded",
    )
    require(
        notice.get("record_id") == dermatology["id"]
        and notice.get("recipient") == "appointment desk"
        and notice.get("receipt") == cancellation["receipt"]
        and notice.get("delivered") is True,
        "the sole notice was not tied to the successful cancellation",
    )
    return cancellation, notice


def verify_state(
    initial: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
    hearing: dict[str, Any],
    dermatology: dict[str, Any],
    cancellation: dict[str, Any],
    notice: dict[str, Any],
) -> None:
    require(set(current) == set(initial), "appointments were created or deleted")
    for record_id, original in initial.items():
        if record_id == dermatology["id"]:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = CANCELLATION_REASON
            require(
                current[record_id] == expected,
                "Dermatology follow-up was not changed exactly as required",
            )
        else:
            require(
                current[record_id] == original,
                f"untargeted appointment {record_id} was modified",
            )
    require(current[hearing["id"]] == hearing, "Hearing test was changed")

    receipts = load_document(STATE_DIR / "receipts.json", "receipts")["receipts"]
    require(len(receipts) == 1, "expected exactly one cancellation receipt")
    receipt = receipts[0]
    require(
        receipt
        == {
            "receipt": cancellation["receipt"],
            "appointment_id": dermatology["id"],
            "from_status": "requested",
            "to_status": "cancelled",
            "reason": CANCELLATION_REASON,
            "consumed": True,
        },
        "cancellation receipt does not prove the mutation-notice dependency",
    )

    initial_notifications = load_document(
        INITIAL_NOTIFICATIONS_PATH, "notifications"
    )["notifications"]
    notifications = load_document(STATE_DIR / "notifications.json", "notifications")[
        "notifications"
    ]
    require(
        len(notifications) == len(initial_notifications) + 1,
        "expected exactly one new appointment notice",
    )
    require(
        notifications[: len(initial_notifications)] == initial_notifications,
        "pre-existing notifications were modified",
    )
    notification = notifications[-1]
    require(
        notification.get("ordinal") == len(initial_notifications) + 1
        and notification.get("recipient") == "appointment desk"
        and notification.get("appointment_id") == dermatology["id"]
        and notification.get("mutation_receipt") == cancellation["receipt"],
        "appointment notice has the wrong recipient or mutation evidence",
    )
    subject = notification.get("subject")
    message = notification.get("message")
    require(
        isinstance(subject, str)
        and isinstance(message, str)
        and bool(subject.strip())
        and bool(message.strip()),
        "appointment notice has no outcome",
    )
    outcome = (subject + " " + message).casefold()
    require(
        "dermatology follow-up" in outcome
        and "robin park" in outcome
        and "cancelled" in outcome
        and CANCELLATION_REASON.casefold() in outcome,
        "appointment notice does not accurately state the outcome",
    )
    require(
        notice.get("message_sha256")
        == hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "notification body does not match the signed operation history",
    )


def main() -> int:
    try:
        verify_protected_inputs()
        initial_document = load_document(INITIAL_APPOINTMENTS_PATH, "appointments")
        current_document = load_document(STATE_DIR / "appointments.json", "appointments")
        initial = appointment_map(initial_document, "protected initial appointments")
        current = appointment_map(current_document, "current appointments")
        hearing = find_unique(initial, HEARING_KEY)
        dermatology = find_unique(initial, DERMATOLOGY_KEY)
        require(
            dermatology.get("status") == "requested",
            "protected conditional branch does not begin requested",
        )
        events = load_audit()
        cancellation, notice = verify_execution(events, hearing, dermatology)
        verify_state(
            initial,
            current,
            hearing,
            dermatology,
            cancellation,
            notice,
        )
    except (VerificationError, OSError, ValueError, TypeError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: confirmed conditional cancellation and dependent appointment-desk notice followed parallel resolution"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
