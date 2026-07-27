#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi format-sensitivity task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "commerce-audit.jsonl"
PAYLOAD_PATH = ROOT / "tool_call.json"
AUDIT_KEY = b"moonshiner-pi-commerce-format-0006-v1"
EXPECTED_PAYLOAD = b'{"tool":"commerce_get","arguments":{"id":"com-106"}}\n'
EXPECTED_PAYLOAD_DIGEST = hashlib.sha256(EXPECTED_PAYLOAD).hexdigest()

EXPECTED_HASHES = {
    "commerce-bridge": "ad3da76140e1f1e801481ac47048fdf46cc011c97291c965298f90a538a49b49",
    ".commerce/orders.json": "55253340b0efffedbac7928e87a3f87ccf05259d6d00015f39c872b65fc7a70b",
    "queue/incoming.xml": "faca17dcefeafa64b61a23ab8771b48ff15db05bea100cb38ab0661c02a9be04",
    "README.md": "e619ba9b45c49884a95adbdc8b82686aad67a014f68390d14da181f950a7f57c",
    ".gitignore": "1a2373cc3beda58033dcf768c95f6aab5c2b0debfb0f55b05b9ba4829e3642f4",
}
ALLOWED_WORKSPACE_PATHS = {
    ".commerce",
    ".commerce/orders.json",
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/commerce-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "commerce-bridge",
    "queue",
    "queue/incoming.xml",
    "tool_call.json",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected commerce input changed: {relative}")


def verify_workspace_paths() -> None:
    observed = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.relative_to(ROOT).parts[0] != ".git"
    }
    unexpected = sorted(observed - ALLOWED_WORKSPACE_PATHS)
    if unexpected:
        fail(f"unexpected workspace artifact: {unexpected[0]}")


def verify_payload() -> None:
    if not PAYLOAD_PATH.is_file():
        fail("tool_call.json is missing")
    raw = PAYLOAD_PATH.read_bytes()
    if raw != EXPECTED_PAYLOAD:
        fail("tool_call.json is not the exact queued strict-JSON invocation")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        fail("tool_call.json is not valid JSON")
    if (
        list(payload) != ["tool", "arguments"]
        or list(payload["arguments"]) != ["id"]
        or not isinstance(payload["arguments"]["id"], str)
    ):
        fail("tool_call.json does not preserve the required object shape and type")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine commerce-bridge execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the bridge")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 1:
        fail("the bridge must be invoked exactly once")
    event = events[0]
    if event.get("success") is not True:
        fail("the bridge invocation did not succeed")
    if event.get("operation") != "commerce_get":
        fail("the queue's read-only mode was not honored")
    if event.get("stable_id") != "com-106":
        fail("the queued record-id was not used")
    if event.get("id_json_type") != "string":
        fail("the queued record-id was not preserved as a JSON string")
    if event.get("queue_mode") != "read-only":
        fail("the bridge did not process the queued mode")
    if event.get("result_count") != 1:
        fail("the genuine registry lookup did not resolve exactly one order")
    if event.get("request_sha256") != EXPECTED_PAYLOAD_DIGEST:
        fail("the executed invocation differs from tool_call.json")
    if event.get("request_path") != "tool_call.json":
        fail("the documented deliverable path was not invoked")
    if (
        not isinstance(event.get("process_pid"), int)
        or event.get("process_group_id") != event["process_pid"]
        or event.get("session_id") != event["process_pid"]
        or event.get("parent_start_ticks") == "unavailable"
    ):
        fail("the bridge was not executed as a direct isolated Bash action")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_paths()
    verify_payload()
    verify_execution(load_events())
    print(
        "PASS: strict queued invocation and genuine Pi commerce-bridge "
        "execution satisfy all constraints"
    )


if __name__ == "__main__":
    main()
