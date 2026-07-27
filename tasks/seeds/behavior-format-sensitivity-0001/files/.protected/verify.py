#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi format-sensitivity seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "calendar-audit.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
RESPONSE_PATH = ROOT / "response.json"

EXPECTED_HASHES = {
    ".gitignore": "bd0c8ea418d3aa5af1293b914023cb7b53dfa9071abef0788b5721a66f7d76f3",
    "README.md": "eaf0c5f8ffd662b913e3895f64d2ac27a374f37511285ec4f651f3327b24a2e3",
    "calendarctl": "9ea04cd27118f5934d36c8dafdfdf646bdc85890c0242a1bc132f27b0fd795bf",
    "queue/lookup.xml": "2d266c890aaa4fa88d906cd4c8a056bd19f13ae3cedc349cc89d38ceb464d807",
    ".calendar/records.json": "c0e66a732a97fe3f8531b236bd6d39afebdf970962b9e6cabff0afcfaaf8bb9b",
    ".protected/audit.key": "a267e770bdd4fe1cccebc2073915a05e7f44699f30c4c78e7a0107fce845b0f1",
}
EXPECTED_RESPONSE = (
    b'{"arguments":{"mode":"read-only","record_id":"cal-101"},'
    b'"name":"meeting_lookup"}\n'
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected_hash:
            fail(f"supplied calendar input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine calendarctl execution evidence was produced")
    key = KEY_PATH.read_bytes().strip()
    events: list[dict] = []
    for line_number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {line_number} is not valid JSON")
        if not isinstance(signed, dict):
            fail(f"execution event {line_number} is not an object")
        signature = signed.pop("signature", None)
        expected = hmac.new(key, canonical(signed), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"execution event {line_number} was not emitted intact")
        events.append(signed)
    return events


def verify_execution(events: list[dict]) -> None:
    if [event.get("operation") for event in events] != ["help", "invoke"]:
        fail("expected exactly one help call followed by one lookup invocation")
    if any(event.get("success") is not True for event in events):
        fail("the required calendarctl operations did not both succeed")
    help_event, invoke_event = events
    if not (
        isinstance(help_event.get("end_ns"), int)
        and isinstance(invoke_event.get("start_ns"), int)
        and invoke_event["start_ns"] > help_event["end_ns"]
    ):
        fail("the lookup did not follow the completed help call")
    expected_output_hash = hashlib.sha256(EXPECTED_RESPONSE).hexdigest()
    expected_invocation = {
        "request_path": "queue/lookup.xml",
        "record_id": "cal-101",
        "mode": "read-only",
        "matched_record_id": "cal-101",
        "result_count": 1,
        "output_sha256": expected_output_hash,
    }
    observed = {
        field: invoke_event.get(field) for field in expected_invocation
    }
    if observed != expected_invocation:
        fail("the executable did not ground one exact read-only queue lookup")


def verify_response() -> None:
    if not RESPONSE_PATH.is_file():
        fail("response.json is missing")
    raw = RESPONSE_PATH.read_bytes()
    if raw != EXPECTED_RESPONSE:
        fail("response.json is not the exact executable-emitted JSON object")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail("response.json is not strict JSON")
    if not isinstance(payload, dict):
        fail("response.json must contain exactly one JSON object")
    if canonical(payload) + b"\n" != raw:
        fail("response.json is not canonical single-object JSON")


def main() -> None:
    verify_inputs()
    verify_execution(load_events())
    verify_response()
    print("PASS: genuine calendarctl execution produced the exact JSON tool call")


if __name__ == "__main__":
    main()
