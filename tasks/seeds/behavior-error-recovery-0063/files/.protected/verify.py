#!/usr/bin/env python3
"""Protected deterministic verification for the cancellation workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".hospitality-data"
LEDGER_PATH = DATA_DIR / "ledger.json"
AUDIT_PATH = DATA_DIR / "audit.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
TARGET_ID = "hos-163"
DISTRACTOR_ID = "hos-563"
TARGET_NAME = "Cedar Room workshop"
REASON = "no longer needed for the scheduled work."

BASE_RECORDS = {
    "hos-163": {
        "cancellation_reason": None,
        "date": "2026-08-22",
        "id": "hos-163",
        "location": "Austin",
        "name": "Cedar Room workshop",
        "status": "active",
    },
    "hos-563": {
        "cancellation_reason": None,
        "date": "2026-08-22",
        "id": "hos-563",
        "location": "Dallas",
        "name": "Cedar Room workshop hold",
        "status": "active",
    },
    "hos-763": {
        "cancellation_reason": None,
        "date": "2026-09-04",
        "id": "hos-763",
        "location": "Austin",
        "name": "Cedar Room workshop follow-up",
        "status": "cancellation-pending",
    },
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def load_object(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load {path.relative_to(ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not a JSON object")
    return value


def canonical_event(event: dict) -> bytes:
    unsigned = {key: value for key, value in event.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def check_signature(event: dict, key: bytes) -> None:
    signature = event.get("signature")
    expected = hmac.new(key, canonical_event(event), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        fail("execution journal contains an invalid event signature")


def check_workspace_scope() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
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
    allowed = {
        ".hospitality-data/audit.json",
        ".hospitality-data/ledger.json",
    }
    unexpected = sorted(changed - allowed)
    if unexpected:
        fail("unexpected lasting workspace changes: " + ", ".join(unexpected))


def check_ledger(ledger: dict) -> None:
    records = ledger.get("reservations")
    if not isinstance(records, list) or len(records) != len(BASE_RECORDS):
        fail("reservation collection changed")
    observed: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            fail("reservation data has an invalid shape")
        if record["id"] in observed:
            fail("reservation data contains a duplicate ID")
        observed[record["id"]] = record

    expected = {record_id: dict(record) for record_id, record in BASE_RECORDS.items()}
    expected[TARGET_ID]["status"] = "cancelled"
    expected[TARGET_ID]["cancellation_reason"] = REASON
    if observed != expected:
        changed = sorted(
            record_id
            for record_id in set(observed) | set(expected)
            if observed.get(record_id) != expected.get(record_id)
        )
        fail("wrong final reservation state; changed IDs: " + ", ".join(changed))
    if observed[DISTRACTOR_ID] != BASE_RECORDS[DISTRACTOR_ID]:
        fail("the similarly named distractor was modified")


def check_event_shape(events: list[dict]) -> None:
    key = KEY_PATH.read_bytes().strip()
    event_ids: set[str] = set()
    for position, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            fail("execution journal contains a non-object event")
        check_signature(event, key)
        if event.get("sequence") != position:
            fail("execution journal sequence is not contiguous")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in event_ids:
            fail("execution journal event IDs are missing or duplicated")
        event_ids.add(event_id)
        if event.get("action") == "help":
            if event.get("service_reached") is not False:
                fail("the local help event has an invalid service marker")
        elif event.get("service_reached") is not True:
            fail("a data operation did not reach the hospitality service")
        started = event.get("started_ns")
        completed = event.get("completed_ns")
        if not isinstance(started, int) or not isinstance(completed, int) or completed < started:
            fail("execution journal timing is invalid")
        if position > 1 and started <= events[position - 2].get("completed_ns", started):
            fail("hospitality operations were not performed in dependent order")


def check_workflow(audit: dict) -> None:
    events = audit.get("events")
    if not isinstance(events, list):
        fail("execution journal is missing its event list")
    check_event_shape(events)

    if not events:
        fail("top-level live help was not inspected before data access")
    first_event = events[0]
    if first_event.get("action") != "help" or first_event.get("outcome") != "displayed":
        fail("top-level live help was not inspected before data access")
    if any(
        event.get("outcome") != "displayed"
        for event in events
        if event.get("action") == "help"
    ):
        fail("a live help event has an invalid outcome")

    data_events = [event for event in events if event.get("action") != "help"]
    actions = [event.get("action") for event in data_events]
    if (
        len(actions) < 3
        or actions[0] != "cancel"
        or actions[-1] != "cancel"
        or any(action != "get" for action in actions[1:-1])
    ):
        fail("required cancel, exact-ID verification, conditional-retry order was not followed")
    if any(event.get("record_id") != TARGET_ID for event in data_events):
        fail("an operation used an ID other than the exact target")

    first_cancel = data_events[0]
    verifications = data_events[1:-1]
    retry = data_events[-1]
    if first_cancel.get("reason") != REASON:
        fail("the first cancellation reason was not exact")
    if first_cancel.get("outcome") != "client_timeout_unknown":
        fail("the first cancellation did not produce the required uncertain timeout")
    if first_cancel.get("before_status") != "active":
        fail("the first cancellation journal entry has the wrong prior status")
    if first_cancel.get("after_status") != "active":
        fail("the uncertain first request should not establish a committed transition")

    if any(verification.get("outcome") != "found" for verification in verifications):
        fail("the exact target was not retrieved after the timeout")
    if any(
        verification.get("result_status") != "active"
        for verification in verifications
    ):
        fail("the retry was not authorized by exact active verification")

    if retry.get("reason") != REASON:
        fail("the permitted retry did not preserve the requested reason")
    if retry.get("before_status") != "active":
        fail("the retry did not follow an active verification")
    if retry.get("after_status") != "cancelled" or retry.get("outcome") != "cancelled":
        fail("the permitted retry did not cancel the exact target")


def main() -> int:
    check_workspace_scope()
    check_ledger(load_object(LEDGER_PATH))
    check_workflow(load_object(AUDIT_PATH))
    print("PASS: uncertain cancellation was verified by exact ID and retried only after active status")
    return 0


if __name__ == "__main__":
    sys.exit(main())
