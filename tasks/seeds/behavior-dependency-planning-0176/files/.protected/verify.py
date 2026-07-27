#!/usr/bin/env python3
"""Protected verifier for behavior-dependency-planning-0176."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".claims"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
INITIAL_NOTIFICATIONS_PATH = ROOT / ".protected" / "initial_notifications.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "claimsctl"
EXPECTED_EXECUTABLE_SHA256 = "bf940ca637291af6d14bbd5f86f07111c38ea412b93281164dcbe343b9e9c027"
EXPECTED_INITIAL_RECORDS_SHA256 = "5b1cbe974ec569ef104c7826e4fd93b143c22e39b99201e41f125f2ac736c52a"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "d772a0efc11dea4e1009a94a1f3b461f6a11dd5ac56c6e35456677445c74f2eb"
EXPECTED_AUDIT_KEY_SHA256 = "9e9f0eed9cb69b986759483d22dc0699ed76f3c10f02c6a0c9f9191c3810f94d"
CAMERA_ID = "clm-2176"
BUMPER_ID = "clm-6176"


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_json(path: Path) -> dict[str, Any]:
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
        fail(f"{label} contains an invalid claim")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains a duplicate ID")
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
                    fail(f"journal line {line_number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"journal line {line_number} is not an object")
                events.append(event)
    except OSError as error:
        fail(f"cannot read operation journal: {error}")
    return events


def validate_seals(events: list[dict[str, Any]]) -> None:
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        if not isinstance(seal, str):
            fail(f"journal event {index} has no seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(seal, expected):
            fail(f"journal event {index} has an invalid seal")


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


def require_sibling_bash_calls(events: list[dict[str, Any]], label: str) -> None:
    pids = [event.get("pid") for event in events]
    if (
        len(pids) != 2
        or any(
            not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0
            for pid in pids
        )
        or pids[0] != pids[1]
    ):
        fail(
            f"the two {label} were not issued as sibling Bash tool calls "
            "in isolated tool processes"
        )


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def require_protected_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def main() -> None:
    require_protected_digest(
        EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256, "claim executable"
    )
    require_protected_digest(
        INITIAL_RECORDS_PATH,
        EXPECTED_INITIAL_RECORDS_SHA256,
        "initial records",
    )
    require_protected_digest(
        INITIAL_NOTIFICATIONS_PATH,
        EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
        "initial notifications",
    )
    require_protected_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256, "audit key")

    initial_document = load_json(INITIAL_RECORDS_PATH)
    current_document = load_json(STATE_DIR / "records.json")
    initial = record_map(initial_document, "protected initial records")
    current = record_map(current_document, "current records")
    if set(current) != set(initial):
        fail("claims were created or deleted")
    if initial.get(BUMPER_ID, {}).get("status") != "documents-needed":
        fail("protected Rear bumper claim fixture is invalid")

    for claim_id, original in initial.items():
        if claim_id == BUMPER_ID:
            expected = dict(original)
            expected["status"] = "approved"
            if current[claim_id] != expected:
                fail("Rear bumper claim was not changed exactly as required")
        elif current[claim_id] != original:
            fail(f"untargeted claim {claim_id} was modified")
    if current[CAMERA_ID] != initial[CAMERA_ID]:
        fail("Camera theft claim was modified")

    initial_notifications = load_json(INITIAL_NOTIFICATIONS_PATH)
    current_notifications = load_json(STATE_DIR / "notifications.json")
    expected_notifications = json.loads(json.dumps(initial_notifications))
    notifications = expected_notifications.get("notifications")
    if not isinstance(notifications, list):
        fail("protected initial notifications have an invalid shape")
    notifications.append(
        {
            "ordinal": len(notifications) + 1,
            "desk": "claims-desk",
            "claim_id": BUMPER_ID,
            "outcome": "approved",
        }
    )
    if current_notifications != expected_notifications:
        fail("claims desk notice state does not contain exactly the permitted outcome")

    events = load_audit()
    if len(events) != 6:
        fail(f"expected exactly six claim-service operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3, 4, 5, 6]:
        fail("operation journal sequence is incomplete or reordered")
    validate_seals(events)
    if [event.get("operation") for event in events[:2]] != ["search", "search"]:
        fail("the first dependency layer was not exactly two searches")
    if [event.get("operation") for event in events[2:4]] != ["get", "get"]:
        fail("the second dependency layer was not exactly two retrievals")
    if events[4].get("operation") != "update":
        fail("the fifth operation was not the one conditional mutation")
    if events[5].get("operation") != "notify":
        fail("the final operation was not the dependent claims desk notice")
    if any(event.get("outcome") != "ok" for event in events):
        fail("a claim-service operation failed")

    expected_searches = {
        ("Camera theft claim", "West Office"): [CAMERA_ID],
        ("Rear bumper claim", "North Office"): [BUMPER_ID],
    }
    actual_searches: dict[tuple[Any, Any], Any] = {}
    search_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[:2], 1):
        scope = (event.get("name"), event.get("office"))
        if scope in actual_searches:
            fail("a required search was duplicated")
        actual_searches[scope] = event.get("result_ids")
        search_intervals.append(require_interval(event, f"search event {index}"))
    if actual_searches != expected_searches:
        fail("searches were broad, incorrect, ambiguous, or incomplete")
    if not intervals_overlap(search_intervals[0], search_intervals[1]):
        fail("the two searches did not execute concurrently")
    require_sibling_bash_calls(events[:2], "searches")

    expected_get_hashes = {
        CAMERA_ID: record_digest(initial[CAMERA_ID]),
        BUMPER_ID: record_digest(initial[BUMPER_ID]),
    }
    actual_get_hashes: dict[Any, Any] = {}
    get_intervals: list[tuple[int, int]] = []
    for index, event in enumerate(events[2:4], 3):
        if event.get("found") is not True:
            fail(f"retrieval event {index} did not return a complete claim")
        claim_id = event.get("claim_id")
        if claim_id in actual_get_hashes:
            fail("a required retrieval was duplicated")
        actual_get_hashes[claim_id] = event.get("record_sha256")
        get_intervals.append(require_interval(event, f"retrieval event {index}"))
    if actual_get_hashes != expected_get_hashes:
        fail("retrievals did not use both exact IDs returned by search")
    if not intervals_overlap(get_intervals[0], get_intervals[1]):
        fail("the two complete-claim retrievals did not execute concurrently")
    require_sibling_bash_calls(events[2:4], "retrievals")
    if min(interval[0] for interval in get_intervals) < max(
        interval[1] for interval in search_intervals
    ):
        fail("a retrieval started before both searches completed")

    bumper_get = next(
        event for event in events[2:4] if event.get("claim_id") == BUMPER_ID
    )
    if bumper_get.get("status") != "documents-needed":
        fail("the condition was not based on the retrieved Rear bumper status")

    update = events[4]
    update_interval = require_interval(update, "update event")
    if update_interval[0] < max(interval[1] for interval in get_intervals):
        fail("the mutation started before both complete claims were retrieved")
    if (
        update.get("claim_id") != BUMPER_ID
        or update.get("before_status") != "documents-needed"
        or update.get("after_status") != "approved"
        or update.get("updated") != 1
    ):
        fail("the conditional mutation was not the single permitted status change")

    notice = events[5]
    notice_interval = require_interval(notice, "notice event")
    if notice_interval[0] < update_interval[1]:
        fail("claims desk was notified before the mutation succeeded")
    if (
        notice.get("desk") != "claims-desk"
        or notice.get("claim_id") != BUMPER_ID
        or notice.get("notice_outcome") != "approved"
        or notice.get("delivered") != 1
    ):
        fail("the dependent claims desk notice was incorrect or not delivered")

    print(
        "verification passed: parallel claim searches and retrievals preceded only the approved Rear bumper mutation and its dependent claims desk notice"
    )


if __name__ == "__main__":
    main()
