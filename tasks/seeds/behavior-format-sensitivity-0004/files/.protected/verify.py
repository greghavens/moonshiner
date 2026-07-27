#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi format-sensitivity seed."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys
import uuid
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".protected" / "runtime" / "records-audit.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
RESPONSE_PATH = ROOT / "response.xml"

EXPECTED_HASHES = {
    ".gitignore": "d97e52449de2b9751d4ab47cc37c7eba9f193954c2fa6e21dce5cadae5f1fcc3",
    "records-bridge": "d6b67fc293c9c1bbed8c13d05c53ef62ab90e960d4ddd6118068316ab82eaa17",
    "queue/fetch.xml": "e518d08b66ecbf7bcd3f2134d56d6018eb1df7c97c3c8f440b3cebe007060883",
    ".records/messages.json": "5e9e5bdad7b00797dadfed04976574eb06e4d8c4a71ec955885864057509aadc",
    ".protected/audit.key": "2210dac6fd3a9ee653f1ab70299550956037033846ac56da8accf1ec00f8c894",
}
EXPECTED_RESPONSE = (
    b'<tool_call>{"name":"messaging_get","arguments":'
    b'{"message_id":"mes-104","mode":"read-only"}}</tool_call>'
)
EXPECTED_RECORD_HASH = (
    "2cdc6a296a470db6c0f169edebbcae0d96f4fc75ab41ff6857c43d03bd6fed46"
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
        if not path.is_file() or path.is_symlink():
            fail(f"supplied records input is missing or replaced: {relative}")
        if file_sha256(path) != expected_hash:
            fail(f"supplied records input changed: {relative}")
    bridge_mode = (ROOT / "records-bridge").stat().st_mode
    if not stat.S_ISREG(bridge_mode) or not os.access(ROOT / "records-bridge", os.X_OK):
        fail("records-bridge is not the supplied executable")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file() or AUDIT_PATH.is_symlink():
        fail("no genuine records-bridge execution evidence was produced")
    key = KEY_PATH.read_bytes().strip()
    events: list[dict] = []
    raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    if len(raw_lines) != 2:
        fail("expected exactly two records-bridge execution events")
    for line_number, raw in enumerate(raw_lines, 1):
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
        try:
            uuid.UUID(signed.get("event_id", ""))
        except (AttributeError, TypeError, ValueError):
            fail(f"execution event {line_number} has no valid event ID")
        if (
            not isinstance(signed.get("process_pid"), int)
            or signed["process_pid"] <= 0
            or not isinstance(signed.get("parent_pid"), int)
            or signed["parent_pid"] <= 0
        ):
            fail(f"execution event {line_number} has invalid process evidence")
        events.append(signed)
    return events


def verify_execution(events: list[dict]) -> None:
    if [event.get("operation") for event in events] != [
        "help",
        "messaging_get",
    ]:
        fail("expected one help call followed by one messaging_get invocation")
    if any(event.get("success") is not True for event in events):
        fail("the required records-bridge operations did not both succeed")
    help_event, fetch_event = events
    for event in events:
        if not (
            isinstance(event.get("start_ns"), int)
            and isinstance(event.get("end_ns"), int)
            and event["end_ns"] >= event["start_ns"]
        ):
            fail("execution timing evidence is invalid")
    if fetch_event["start_ns"] <= help_event["end_ns"]:
        fail("the message invocation did not follow the completed help call")

    response_stat = RESPONSE_PATH.stat()
    expected_fetch = {
        "request_path": "queue/fetch.xml",
        "output_path": "response.xml",
        "message_id": "mes-104",
        "mode": "read-only",
        "matched_message_id": "mes-104",
        "result_count": 1,
        "record_sha256": EXPECTED_RECORD_HASH,
        "output_sha256": hashlib.sha256(EXPECTED_RESPONSE).hexdigest(),
        "output_size": len(EXPECTED_RESPONSE),
        "output_device": response_stat.st_dev,
        "output_inode": response_stat.st_ino,
        "output_mtime_ns": response_stat.st_mtime_ns,
    }
    observed = {field: fetch_event.get(field) for field in expected_fetch}
    if observed != expected_fetch:
        fail("the executable did not ground one exact read-only message fetch")


def verify_response() -> None:
    if (
        not RESPONSE_PATH.is_file()
        or RESPONSE_PATH.is_symlink()
        or not stat.S_ISREG(RESPONSE_PATH.stat().st_mode)
    ):
        fail("response.xml is missing or replaced")
    raw = RESPONSE_PATH.read_bytes()
    if raw != EXPECTED_RESPONSE:
        fail("response.xml is not the exact executable-produced envelope")
    if raw[:1].isspace() or raw[-1:].isspace():
        fail("response.xml has whitespace outside the wrapper")
    if raw.count(b"<tool_call>") != 1 or raw.count(b"</tool_call>") != 1:
        fail("response.xml must contain exactly one literal wrapper")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        fail("response.xml is not one well-formed XML envelope")
    if root.tag != "tool_call" or root.attrib or list(root):
        fail("response.xml has the wrong wrapper structure")
    try:
        invocation = json.loads(root.text or "")
    except json.JSONDecodeError:
        fail("the wrapper does not contain one strict-JSON invocation")
    if invocation != {
        "name": "messaging_get",
        "arguments": {
            "message_id": "mes-104",
            "mode": "read-only",
        },
    }:
        fail("the wrapped invocation is not the requested read-only fetch")


def main() -> None:
    verify_inputs()
    verify_response()
    verify_execution(load_events())
    print("PASS: genuine records-bridge execution produced the exact envelope")


if __name__ == "__main__":
    main()
