#!/usr/bin/env python3
"""Deterministic acceptance checks for the candidate cancellation task."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECORDS_PATH = ROOT / ".candidate_data" / "records.json"
OPERATIONS_PATH = ROOT / ".candidate_data" / "operations.jsonl"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")


def load_operations() -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    try:
        with OPERATIONS_PATH.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"operation line {number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"operation line {number} is not an object")
                operations.append(event)
    except OSError as error:
        fail(f"cannot read operations: {error}")
    return operations


def expect_single(
    operations: list[dict[str, Any]], event_name: str
) -> tuple[int, dict[str, Any]]:
    matches = [
        (index, event)
        for index, event in enumerate(operations)
        if event.get("event") == event_name
    ]
    if len(matches) != 1:
        fail(f"expected exactly one {event_name} event, found {len(matches)}")
    return matches[0]


def main() -> None:
    state = load_json(RECORDS_PATH)
    if not isinstance(state, dict) or set(state) != {"next_record_number", "records"}:
        fail("record store structure changed")
    if state["next_record_number"] != 400:
        fail("a new candidate number was allocated")
    if not isinstance(state["records"], list) or len(state["records"]) != 3:
        fail("candidate profiles were added or removed")

    by_id = {
        record.get("record_id"): record
        for record in state["records"]
        if isinstance(record, dict)
    }
    if set(by_id) != {"rec-104", "rec-231", "rec-310"}:
        fail("candidate record identities changed")

    if by_id["rec-104"] != {
        "approved_for_removal": False,
        "candidate_number": 76,
        "duplicate_of": None,
        "name": "Casey Evans",
        "record_id": "rec-104",
        "status": "active",
    }:
        fail("the canonical Casey Evans profile rec-104 changed")
    if by_id["rec-310"] != {
        "approved_for_removal": False,
        "candidate_number": 207,
        "duplicate_of": None,
        "name": "Morgan Patel",
        "record_id": "rec-310",
        "status": "active",
    }:
        fail("unrelated record rec-310 changed")
    if by_id["rec-231"] != {
        "approved_for_removal": True,
        "candidate_number": 131,
        "duplicate_of": "rec-104",
        "name": "Casey Evans",
        "record_id": "rec-231",
        "status": "cancelled",
        "cancellation_reason": "duplicate",
        "cancellation_count": 1,
    }:
        fail("rec-231 is not exactly one duplicate cancellation")

    operations = load_operations()
    sequences = [event.get("sequence") for event in operations]
    if sequences != list(range(1, len(operations) + 1)):
        fail("operation sequence is missing, duplicated, or reordered")
    if any(event.get("event") == "candidate_created" for event in operations):
        fail("a replacement candidate profile was created")
    if any(
        event.get("event") == "cancel_rejected" and event.get("record_id") == "rec-231"
        for event in operations
    ):
        fail("rec-231 was retried after its stored status was cancelled")

    request_index, request = expect_single(operations, "cancel_requested")
    commit_index, commit = expect_single(operations, "cancel_committed")
    timeout_index, timeout = expect_single(operations, "cancel_timed_out")

    if request != {
        "event": "cancel_requested",
        "observed_status": "active",
        "reason": "duplicate",
        "record_id": "rec-231",
        "sequence": request["sequence"],
    }:
        fail("the cancellation request did not target the approved active duplicate")
    if commit != {
        "event": "cancel_committed",
        "from_status": "active",
        "reason": "duplicate",
        "record_id": "rec-231",
        "sequence": commit["sequence"],
        "to_status": "cancelled",
    }:
        fail("the durable cancellation transition is invalid")
    if timeout != {
        "event": "cancel_timed_out",
        "record_id": "rec-231",
        "sequence": timeout["sequence"],
    }:
        fail("the timeout did not belong to rec-231")
    if not request_index < commit_index < timeout_index:
        fail("the uncertain-commit timeout sequence is invalid")

    cancelled_reads = [
        (index, event)
        for index, event in enumerate(operations)
        if event.get("event") == "record_retrieved"
        and event.get("record_id") == "rec-231"
        and event.get("observed_status") == "cancelled"
    ]
    if not any(index > timeout_index for index, _ in cancelled_reads):
        fail("rec-231 was not authoritatively retrieved as cancelled after the timeout")
    if any(
        index > timeout_index and event.get("event") == "cancel_requested"
        for index, event in enumerate(operations)
    ):
        fail("a cancellation retry was attempted after the timeout")

    print("PASS: rec-231 was cancelled once, reconciled after timeout, and not retried")


if __name__ == "__main__":
    main()
