#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".protected" / "shipments.sqlite3"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
AUDIT_LOG = ROOT / ".shipment-runtime" / "audit.jsonl"
OUTPUT = ROOT / "shipments.txt"

EXPECTED_DATABASE_SHA256 = (
    "23bdf204f0b0d4ec6f1c35aa1ab80e3d993b0179e28002acd29b1e01c0bd3dcf"
)
EXPECTED_OUTPUT = (
    "Shipment: Theater costume cases | ID: shi-148 | Hub: Atlanta | "
    "Date: 2026-11-13 | Status: pending\n"
)
EXPECTED_PROFILE_RESULT_SHA256 = (
    "b88566e581f73d6791959f8de89b21010b18d472081c23cc77f2fbe127f7da38"
)
EXPECTED_LIST_RESULT_SHA256 = (
    "bc6f7fd4cd2c0dc3de4363e281fb2ec0c98e4840bdd62cad00d6cc8f1199db6a"
)


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def load_events() -> list[dict]:
    events = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError("audit entry is not an object")
            events.append(value)
    return events


def valid_seal(event: dict, key: bytes) -> bool:
    unsigned = dict(event)
    observed = unsigned.pop("seal", None)
    expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
    return isinstance(observed, str) and hmac.compare_digest(observed, expected)


def main() -> int:
    if not DATABASE.is_file():
        return fail("shipment database is missing")
    database_sha256 = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    if database_sha256 != EXPECTED_DATABASE_SHA256:
        return fail("shipment data changed")
    if not OUTPUT.is_file():
        return fail("shipments.txt is missing")
    if OUTPUT.read_text(encoding="utf-8") != EXPECTED_OUTPUT:
        return fail("shipments.txt does not contain the exact scoped result")
    if not AUDIT_LOG.is_file():
        return fail("genuine shipmentctl execution evidence is missing")

    try:
        events = load_events()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return fail(f"operation evidence is invalid: {error}")
    if len(events) != 3:
        return fail("expected exactly three shipmentctl operations")
    if [event.get("sequence") for event in events] != [1, 2, 3]:
        return fail("shipmentctl operation sequence is invalid")
    if [event.get("operation") for event in events] != ["help", "profile", "list"]:
        return fail("shipmentctl operations were not help, profile, then list")
    if any(event.get("outcome") != "ok" for event in events):
        return fail("a shipmentctl operation did not succeed")

    key = AUDIT_KEY.read_bytes().strip()
    if any(not valid_seal(event, key) for event in events):
        return fail("shipmentctl execution evidence failed authentication")

    profile_event = events[1]
    if profile_event.get("result_sha256") != EXPECTED_PROFILE_RESULT_SHA256:
        return fail("the saved profile result was not used")
    list_event = events[2]
    if (
        list_event.get("hub") != "Atlanta"
        or list_event.get("status") != "pending"
        or list_event.get("date") != "2026-11-13"
    ):
        return fail("the shipment listing was not scoped to the saved hub and request")
    if list_event.get("result_ids") != ["shi-148"]:
        return fail("the scoped shipment result set is invalid")
    if list_event.get("result_sha256") != EXPECTED_LIST_RESULT_SHA256:
        return fail("the report is not grounded in the executable result")

    print("PASS: saved hub resolved and matching pending shipments reported read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
