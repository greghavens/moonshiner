#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi hospitality retrieval task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / ".hospitality" / "reservations.json"
REQUEST_PATH = ROOT / "inbox" / "pending-retrieval.json"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "execution.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
MARKER_PATH = ROOT / ".reference_solution"
REFERENCE_RESPONSE_PATH = ROOT / ".protected" / "runtime" / "reference-response.txt"
MARKER_SHA256 = "7d6975a67800edefa029f4a07478937bd78d36e68df58f23029a2fc1a2ebb3a0"

EXPECTED_HASHES = {
    ".gitignore": "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e",
    "README.md": "d1b0c5cab327b0330fd68ed597648a28e9c5979d17bb33d5fd0ace40ee135edd",
    "reservationctl": "16bd641cf7ae57e2d98281883f8e237ecccd36bc6bcb02489409688dd46c759e",
    "inbox/pending-retrieval.json": "09c2df230ed5f108b2c82aa2e23ba2b49a858c0413fed2cb7943e97c8519678d",
    ".hospitality/reservations.json": "06b823375c136fd07c15d75df8f74d48e50a5df1ed55c4d625dd6b2a190d317e",
    ".protected/audit.key": "6d6813a0665687dad8f422b6d26979a5a2cc938db1abd35b6c2aec01768b6b7e",
    ".protected/reference_setup.py": "364df6c832a0680c25c35f53e3e354a62be56a10524615586c34c7aa00fc81c9",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")


def verify_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected_hash:
            fail(f"supplied input changed: {relative}")


def load_expected() -> tuple[str, str, dict[str, Any], bytes]:
    try:
        request = json.loads(REQUEST_PATH.read_text(encoding="utf-8"))
        store = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load protected reservation inputs: {error}")
    if not isinstance(request, dict):
        fail("pending request is not an object")
    stable_id = request.get("stable_id")
    access = request.get("access")
    if not isinstance(stable_id, str) or access != "read-only":
        fail("pending request is not a read-only stable-ID retrieval")
    records = store.get("reservations") if isinstance(store, dict) else None
    if not isinstance(records, list):
        fail("reservation store is invalid")
    matches = [
        record
        for record in records
        if isinstance(record, dict) and record.get("id") == stable_id
    ]
    if len(matches) != 1:
        fail("pending stable ID does not resolve uniquely")
    record = matches[0]
    response = canonical(
        {
            "record": record,
            "response_line": f"hospitality_get(id={json.dumps(stable_id)})",
        }
    ) + b"\n"
    return stable_id, access, record, response


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine reservationctl execution evidence was produced")
    try:
        raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot read execution evidence: {error}")
    if any(not line for line in raw_lines):
        fail("execution evidence contains a blank line")
    events: list[dict[str, Any]] = []
    previous_seal: str | None = None
    key = KEY_PATH.read_bytes().strip()
    for line_number, raw in enumerate(raw_lines, 1):
        try:
            signed = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {line_number} is not valid JSON")
        if not isinstance(signed, dict):
            fail(f"execution event {line_number} is not an object")
        seal = signed.pop("seal", None)
        expected_seal = hmac.new(
            key, canonical(signed), hashlib.sha256
        ).hexdigest()
        if not isinstance(seal, str) or not hmac.compare_digest(
            seal, expected_seal
        ):
            fail(f"execution event {line_number} was not emitted intact")
        if signed.get("sequence") != line_number:
            fail(f"execution event {line_number} has an invalid sequence")
        if signed.get("previous_seal") != previous_seal:
            fail(f"execution event {line_number} breaks the evidence chain")
        previous_seal = seal
        signed["seal"] = seal
        events.append(signed)
    return events


def verify_interval(event: dict[str, Any]) -> None:
    started = event.get("started_ns")
    finished = event.get("finished_ns")
    process_id = event.get("process_id")
    if (
        not isinstance(started, int)
        or isinstance(started, bool)
        or not isinstance(finished, int)
        or isinstance(finished, bool)
        or started >= finished
        or not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
    ):
        fail("execution event has an invalid process interval")
    try:
        uuid.UUID(event.get("event_id"))
    except (AttributeError, TypeError, ValueError):
        fail("execution event has an invalid event ID")


def verify_execution(
    events: list[dict[str, Any]],
    stable_id: str,
    access: str,
    record: dict[str, Any],
    response: bytes,
) -> None:
    if len(events) != 2:
        fail(f"expected exactly two executable calls, found {len(events)}")
    help_event, retrieve_event = events
    for event in events:
        verify_interval(event)
    if not (
        help_event.get("operation") == "help"
        and help_event.get("argv") == ["--help"]
        and help_event.get("pending_request_count") == 1
        and help_event.get("success") is True
    ):
        fail("the first call was not exactly one successful top-level help call")
    if not (
        isinstance(retrieve_event.get("started_ns"), int)
        and isinstance(help_event.get("finished_ns"), int)
        and retrieve_event["started_ns"] > help_event["finished_ns"]
    ):
        fail("the retrieval did not follow the completed help call")

    store_hash = file_sha256(STORE_PATH)
    expected_retrieval = {
        "operation": "retrieve",
        "argv": [
            "retrieve",
            "--request",
            "inbox/pending-retrieval.json",
        ],
        "success": True,
        "request_path": "inbox/pending-retrieval.json",
        "request_sha256": file_sha256(REQUEST_PATH),
        "stable_id": stable_id,
        "access": access,
        "found": True,
        "record_sha256": hashlib.sha256(canonical(record)).hexdigest(),
        "response_sha256": hashlib.sha256(response).hexdigest(),
        "state_before_sha256": store_hash,
        "state_after_sha256": store_hash,
    }
    observed = {
        field: retrieve_event.get(field) for field in expected_retrieval
    }
    if observed != expected_retrieval:
        fail("the executable did not perform the exact grounded full-record retrieval")


def verify_reference(stable_id: str) -> None:
    if not MARKER_PATH.exists():
        if REFERENCE_RESPONSE_PATH.exists():
            fail("unexpected reference-only response artifact")
        return
    if (
        not MARKER_PATH.is_file()
        or file_sha256(MARKER_PATH) != MARKER_SHA256
    ):
        fail("reference patch marker is invalid")
    expected = f"hospitality_get(id={json.dumps(stable_id)})\n".encode("utf-8")
    if (
        not REFERENCE_RESPONSE_PATH.is_file()
        or REFERENCE_RESPONSE_PATH.read_bytes() != expected
    ):
        fail("reference execution did not produce the exact response line")


def main() -> None:
    verify_inputs()
    stable_id, access, record, response = load_expected()
    verify_execution(
        load_events(),
        stable_id,
        access,
        record,
        response,
    )
    verify_reference(stable_id)
    print(
        "verification passed: genuine help and one read-only full-record "
        "retrieval produced the grounded response line"
    )


if __name__ == "__main__":
    main()
