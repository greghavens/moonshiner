#!/usr/bin/env python3
"""Protected acceptance verifier for the records-bridge handoff."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_RESPONSE = (
    b'<tool_call>public-services_get(id="pub-119")</tool_call>'
)


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    response_path = ROOT / "response.xml"
    if not response_path.is_file():
        fail("response.xml is missing")
    if response_path.read_bytes() != EXPECTED_RESPONSE:
        fail("response.xml does not match the literal-envelope contract")

    applications = load_json(ROOT / ".records" / "applications.json")
    initial_applications = load_json(
        ROOT / ".protected" / "initial_applications.json"
    )
    if applications != initial_applications:
        fail("application state changed during a read-only retrieval")

    current_notifications = load_json(ROOT / ".records" / "notifications.json")
    initial_notifications = load_json(
        ROOT / ".protected" / "initial_notifications.json"
    )
    if current_notifications != initial_notifications:
        fail("notification state changed")

    audit_path = ROOT / ".records" / "audit.jsonl"
    if not audit_path.is_file():
        fail("no executable audit evidence was recorded")
    lines = [
        line
        for line in audit_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if len(lines) != 2:
        fail("help and exactly one records operation must be executed")

    key = bytes.fromhex(
        (ROOT / ".protected" / "audit.key").read_text(encoding="utf-8").strip()
    )
    events = []
    for line in lines:
        try:
            sealed_event = json.loads(line)
        except json.JSONDecodeError:
            fail("an executable audit record is malformed")
        if not isinstance(sealed_event, dict) or "seal" not in sealed_event:
            fail("an executable audit record has an unexpected shape")
        seal = sealed_event["seal"]
        event = {
            field: value
            for field, value in sealed_event.items()
            if field != "seal"
        }
        expected_seal = hmac.new(
            key, compact(event).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(str(seal), expected_seal):
            fail("an executable audit seal is invalid")
        events.append(event)

    records = initial_applications.get("applications")
    target = next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("id") == "pub-119"
        ),
        None,
    )
    if target is None:
        fail("protected target record is missing")
    expected_hash = hashlib.sha256(compact(target).encode("utf-8")).hexdigest()
    expected_events = [
        {
            "operation": "help",
            "read_only": True,
            "sequence": 1,
        },
        {
            "found": True,
            "operation": "public-services_get",
            "read_only": True,
            "record_id": "pub-119",
            "record_sha256": expected_hash,
            "sequence": 2,
        },
    ]
    if events != expected_events:
        fail("help was not followed by exactly one required full-record retrieval")

    print(
        "verified: help, one grounded read-only retrieval, and exact literal envelope"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
