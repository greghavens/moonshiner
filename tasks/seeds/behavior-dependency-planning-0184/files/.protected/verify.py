#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0184."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".messages"
PROTECTED_DIR = ROOT / ".protected"
INITIAL_RECORDS_PATH = PROTECTED_DIR / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = PROTECTED_DIR / "initial_notifications.json"
INITIAL_RECEIPTS_PATH = PROTECTED_DIR / "initial_receipts.json"
KEY_PATH = PROTECTED_DIR / "audit.key"
YEAR_END_ID = "msg-284"
WELCOME_ID = "msg-684"
YEAR_END_KEY = ("Year-end donor thanks", "Volunteers")
WELCOME_KEY = ("New-student welcome message", "North Team")
EXPECTED_INITIAL_RECORDS_SHA256 = (
    "63fc5b9bd76f8e3886fad4a3ab1a46fa60573e702491827aacf3f0f87d965c39"
)
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = (
    "fe8c00308dc615f3d536fb38bf17c8af5f9b142d98bd58fd2b5daf78dbf07547"
)
EXPECTED_INITIAL_RECEIPTS_SHA256 = (
    "f95e82e8c8b77b9603bdcb5bbad51e65b32c03410e01d0ecc4766f78b1dfe94c"
)
EXPECTED_KEY_SHA256 = (
    "cce3e77f79a4586e70df090ba572b54a08887985a040532a18d9fb7fafb1e3be"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        print(f"verification failed: {message}", file=sys.stderr)
        raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        require(False, f"cannot read {path.relative_to(ROOT)}: {error}")
    raise AssertionError("unreachable")


def load_document(path: Path, collection: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        require(False, f"cannot read {path.relative_to(ROOT)}: {error}")
    require(isinstance(value, dict), f"{path.relative_to(ROOT)} is not an object")
    require(value.get("version") == 1, f"{path.relative_to(ROOT)} has wrong version")
    items = value.get(collection)
    require(
        isinstance(items, list) and all(isinstance(item, dict) for item in items),
        f"{path.relative_to(ROOT)} has invalid {collection}",
    )
    return value


def record_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    records = document["records"]
    require(
        all(
            isinstance(record.get("id"), str) and bool(record["id"])
            for record in records
        ),
        f"{label} contains a record without a stable ID",
    )
    mapped = {record["id"]: record for record in records}
    require(len(mapped) == len(records), f"{label} contains duplicate IDs")
    return mapped


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
                    require(False, f"audit line {line_number} is invalid: {error}")
                require(
                    isinstance(event, dict),
                    f"audit line {line_number} is not an object",
                )
                events.append(event)
    except OSError as error:
        require(False, f"cannot read audit log: {error}")
    return events


def verify_protected_inputs() -> None:
    require(
        sha256(INITIAL_RECORDS_PATH) == EXPECTED_INITIAL_RECORDS_SHA256,
        "protected initial records were modified",
    )
    require(
        sha256(INITIAL_NOTIFICATIONS_PATH)
        == EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "protected initial notifications were modified",
    )
    require(
        sha256(INITIAL_RECEIPTS_PATH) == EXPECTED_INITIAL_RECEIPTS_SHA256,
        "protected initial receipts were modified",
    )
    require(sha256(KEY_PATH) == EXPECTED_KEY_SHA256, "protected audit key was modified")


def validate_seals(events: list[dict[str, Any]]) -> None:
    key = KEY_PATH.read_bytes().strip()
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        require(
            isinstance(seal, str) and bool(seal),
            f"audit event {index} has no seal",
        )
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(seal, expected),
            f"audit event {index} has an invalid seal",
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
    require(len(events) == 2, f"{label} phase must have exactly two operations")
    process_ids = {event.get("process_id") for event in events}
    parent_ids = {event.get("parent_process_id") for event in events}
    require(
        len(process_ids) == 2 and all(isinstance(value, int) for value in process_ids),
        f"{label} branches were not separate executable processes",
    )
    require(
        len(parent_ids) == 1 and all(isinstance(value, int) for value in parent_ids),
        f"{label} branches were not launched together",
    )
    intervals = [require_interval(event, label) for event in events]
    require(
        max(interval[0] for interval in intervals)
        < min(interval[1] for interval in intervals),
        f"{label} processes did not overlap",
    )


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def find_unique(
    records: dict[str, dict[str, Any]], key: tuple[str, str]
) -> dict[str, Any]:
    matches = [
        record
        for record in records.values()
        if (record.get("title"), record.get("team")) == key
    ]
    require(len(matches) == 1, f"protected lookup is not unique: {key!r}")
    return matches[0]


def verify_operations(
    events: list[dict[str, Any]],
    initial: dict[str, dict[str, Any]],
    year_end: dict[str, Any],
    welcome: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    require(len(events) == 6, f"expected exactly six operations, found {len(events)}")
    require(
        [event.get("sequence") for event in events] == [1, 2, 3, 4, 5, 6],
        "audit sequence is incomplete or reordered",
    )
    require(
        [event.get("operation") for event in events]
        == ["search", "search", "get", "get", "schedule", "notify"],
        "operation dependency layers were missing, reordered, or repeated",
    )
    require(
        all(event.get("outcome") == "ok" for event in events),
        "an operation failed or was skipped",
    )
    validate_seals(events)

    searches = events[:2]
    expected_searches = {
        YEAR_END_KEY: [year_end["id"]],
        WELCOME_KEY: [welcome["id"]],
    }
    observed_searches: dict[tuple[Any, Any], Any] = {}
    for event in searches:
        key = (event.get("title"), event.get("team"))
        require(key not in observed_searches, "a required search was duplicated")
        observed_searches[key] = event.get("result_ids")
    require(
        observed_searches == expected_searches,
        "searches were broad, incorrect, ambiguous, or incomplete",
    )
    verify_parallel_phase(searches, "search")

    gets = events[2:4]
    expected_gets = {
        year_end["id"]: record_digest(initial[year_end["id"]]),
        welcome["id"]: record_digest(initial[welcome["id"]]),
    }
    observed_gets: dict[Any, Any] = {}
    for event in gets:
        require(event.get("found") is True, "a complete-record retrieval failed")
        record_id = event.get("record_id")
        require(record_id not in observed_gets, "a required retrieval was duplicated")
        observed_gets[record_id] = event.get("record_sha256")
        require(
            event.get("status") == initial.get(record_id, {}).get("status"),
            "a retrieval did not capture the initial complete-record status",
        )
    require(
        observed_gets == expected_gets,
        "retrievals did not use both exact IDs returned by search",
    )
    verify_parallel_phase(gets, "retrieval")

    search_intervals = [require_interval(event, "search") for event in searches]
    get_intervals = [require_interval(event, "retrieval") for event in gets]
    require(
        min(interval[0] for interval in get_intervals)
        > max(interval[1] for interval in search_intervals),
        "a retrieval started before both searches completed",
    )

    scheduling = events[4]
    schedule_interval = require_interval(scheduling, "schedule")
    require(
        schedule_interval[0] > max(interval[1] for interval in get_intervals),
        "scheduling started before both complete records were retrieved",
    )
    require(
        scheduling.get("record_id") == welcome["id"]
        and scheduling.get("conditional_status") == "draft"
        and scheduling.get("before_status") == "draft"
        and scheduling.get("after_status") == "scheduled"
        and scheduling.get("scheduled") is True
        and isinstance(scheduling.get("receipt"), str)
        and bool(scheduling["receipt"]),
        "the sole mutation was not the permitted guarded status transition",
    )

    notice = events[5]
    notice_interval = require_interval(notice, "notification")
    require(
        notice_interval[0] > schedule_interval[1],
        "communications lead was notified before scheduling succeeded",
    )
    require(
        notice.get("record_id") == welcome["id"]
        and notice.get("recipient") == "communications lead"
        and notice.get("receipt") == scheduling["receipt"]
        and notice.get("delivered") is True,
        "the sole notice was not tied to the successful scheduling mutation",
    )
    return scheduling, notice


def verify_state(
    initial: dict[str, dict[str, Any]],
    year_end: dict[str, Any],
    welcome: dict[str, Any],
    scheduling: dict[str, Any],
    notice_event: dict[str, Any],
) -> None:
    current = record_map(
        load_document(STATE_DIR / "records.json", "records"), "current records"
    )
    require(set(current) == set(initial), "records were created or deleted")
    for record_id, original in initial.items():
        if record_id == welcome["id"]:
            expected = dict(original)
            expected["status"] = "scheduled"
            require(
                current[record_id] == expected,
                "New-student welcome message was not changed only to scheduled",
            )
        else:
            require(
                current[record_id] == original,
                f"untargeted record {record_id} was modified",
            )
    require(
        current[year_end["id"]] == year_end,
        "Year-end donor thanks was modified",
    )

    initial_receipts = load_document(INITIAL_RECEIPTS_PATH, "receipts")["receipts"]
    receipts = load_document(STATE_DIR / "receipts.json", "receipts")["receipts"]
    require(
        receipts[: len(initial_receipts)] == initial_receipts,
        "pre-existing mutation receipts were modified",
    )
    require(
        len(receipts) == len(initial_receipts) + 1,
        "expected exactly one scheduling receipt",
    )
    receipt = receipts[-1]
    require(
        receipt
        == {
            "consumed": True,
            "from_status": "draft",
            "message_id": welcome["id"],
            "receipt": scheduling["receipt"],
            "to_status": "scheduled",
        },
        "scheduling receipt does not prove the mutation-notice dependency",
    )

    initial_notifications = load_document(
        INITIAL_NOTIFICATIONS_PATH, "notifications"
    )["notifications"]
    notifications = load_document(
        STATE_DIR / "notifications.json", "notifications"
    )["notifications"]
    require(
        notifications[: len(initial_notifications)] == initial_notifications,
        "pre-existing notifications were modified",
    )
    require(
        len(notifications) == len(initial_notifications) + 1,
        "expected exactly one new notification",
    )
    notification = notifications[-1]
    require(
        notification.get("ordinal") == len(initial_notifications) + 1
        and notification.get("recipient") == "communications lead"
        and notification.get("message_id") == welcome["id"]
        and notification.get("mutation_receipt") == scheduling["receipt"],
        "the notice has the wrong recipient, record, or mutation evidence",
    )
    subject = notification.get("subject")
    message = notification.get("message")
    require(
        isinstance(subject, str)
        and bool(subject.strip())
        and isinstance(message, str)
        and bool(message.strip()),
        "the notification has no outcome",
    )
    outcome = f"{subject} {message}".casefold()
    require(
        "new-student welcome message" in outcome
        and "north team" in outcome
        and "draft" in outcome
        and "scheduled" in outcome,
        "the notification does not accurately state the successful outcome",
    )
    require(
        "year-end donor thanks" not in outcome,
        "the notification incorrectly included the unchanged record",
    )
    require(
        notice_event.get("message_sha256")
        == hashlib.sha256(message.encode("utf-8")).hexdigest(),
        "notification audit does not match the delivered outcome",
    )


def main() -> None:
    verify_protected_inputs()
    initial = record_map(
        load_document(INITIAL_RECORDS_PATH, "records"), "protected initial records"
    )
    year_end = find_unique(initial, YEAR_END_KEY)
    welcome = find_unique(initial, WELCOME_KEY)
    require(year_end.get("id") == YEAR_END_ID, "protected Year-end fixture is invalid")
    require(welcome.get("id") == WELCOME_ID, "protected welcome fixture is invalid")
    require(welcome.get("status") == "draft", "protected welcome status is invalid")

    events = load_audit()
    scheduling, notice = verify_operations(
        events, initial, year_end, welcome
    )
    verify_state(initial, year_end, welcome, scheduling, notice)
    print(
        "verification passed: parallel search and retrieval layers preceded the "
        "single guarded scheduling transition and receipt-bound communications-lead notice"
    )


if __name__ == "__main__":
    main()
