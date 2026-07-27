#!/usr/bin/env python3
"""Protected deterministic verification for the title reconciliation task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "register_data" / "titles.json"
KEY_PATH = ROOT / "register_data" / "audit.key"
TOOL_PATH = ROOT / "title_register.py"
RUNTIME_PATH = ROOT / ".register-runtime"
AUDIT_PATH = RUNTIME_PATH / "audit.jsonl"
STATE_PATH = RUNTIME_PATH / "state.json"
OUTBOX_PATH = RUNTIME_PATH / "notifications.jsonl"
REPORT_PATH = ROOT / "title-reconciliation.md"
TARGET_NAME = "River Basin Almanac"
TARGET_LOCATION = "Central"
PROTECTED_HASHES = {
    DATA_PATH: "010b82d6af7d6dc87c624c7383062120a23ab889b34409ce71a138f9f11f8680",
    KEY_PATH: "6fc40ddcad75624c02d26026d07bc097a03cad4bde1e414a53d38e29538eaff9",
    TOOL_PATH: "dcfb9cc1fe53b77fec6c012709ec7a4ae65f3250c7829b6a7484cbfce5b0e7c5",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def verify_protected_assets() -> None:
    for path, expected_hash in PROTECTED_HASHES.items():
        try:
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            fail(f"protected register asset is unavailable: {error}")
        if not hmac.compare_digest(actual_hash, expected_hash):
            fail(f"protected register asset was modified: {path.relative_to(ROOT)}")


def load_expected_record() -> dict:
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        matches = [
            record
            for record in payload["records"]
            if record.get("name") == TARGET_NAME
            and record.get("location") == TARGET_LOCATION
            and record.get("status") == "active"
        ]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        fail(f"protected register fixture is invalid: {error}")
    if len(matches) != 1:
        fail("protected register fixture does not have one exact active target")
    return matches[0]


def load_audit() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no title-register execution evidence was produced")
    try:
        runtime_entries = {path.name for path in RUNTIME_PATH.iterdir()}
    except OSError as error:
        fail(f"title-register runtime is invalid: {error}")
    if runtime_entries != {AUDIT_PATH.name}:
        fail("title-register runtime contains unauthorized changes")
    try:
        lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        fail(f"title-register execution evidence is invalid: {error}")
    if len(events) != 2:
        fail("expected exactly two register data operations")

    key = KEY_PATH.read_bytes().strip()
    for index, event in enumerate(events, start=1):
        if event.get("sequence") != index:
            fail("register operations are not a single ordered sequence")
        signature = event.get("signature")
        unsigned = {key_name: value for key_name, value in event.items()
                    if key_name != "signature"}
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail("register execution evidence did not come from the executable")
    return events


def verify_execution(events: list[dict], expected_record: dict) -> None:
    search, get = events
    if search.get("operation") != "search":
        fail("the first register data operation was not the exact search")
    if search.get("arguments") != {
        "name": TARGET_NAME,
        "location": TARGET_LOCATION,
    }:
        fail("the search did not use the exact requested name and location")
    if search.get("result_ids") != [expected_record["id"]]:
        fail("the exact search did not uniquely resolve the protected target")

    if get.get("operation") != "get":
        fail("the dependent register operation was not a full-record retrieval")
    if get.get("arguments") != {"id": search["result_ids"][0]}:
        fail("the retrieval did not use the stable ID returned by search")
    if get.get("result_ids") != [expected_record["id"]]:
        fail("the full-record retrieval did not return the exact target")

    if STATE_PATH.exists():
        fail("register state was changed")
    if OUTBOX_PATH.exists():
        fail("a notification was sent")


def verify_report(expected_record: dict) -> None:
    if not REPORT_PATH.is_file():
        fail("title-reconciliation.md is missing")
    expected = (
        "| Field | Value |\n"
        "|---|---|\n"
        f"| ID | {expected_record['id']} |\n"
        f"| Name | {expected_record['name']} |\n"
        f"| Location | {expected_record['location']} |\n"
        f"| Status | {expected_record['status']} |\n"
        f"| Date | {expected_record['date']} |\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"could not read title-reconciliation.md: {error}")
    if actual != expected:
        fail("title-reconciliation.md does not match the required compact table")


def main() -> int:
    verify_protected_assets()
    expected_record = load_expected_record()
    events = load_audit()
    verify_execution(events, expected_record)
    verify_report(expected_record)
    print("PASS: exact dependent lookup and compact reconciliation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
