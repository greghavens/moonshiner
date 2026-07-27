#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi shipment retrieval task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any
import uuid


ROOT = Path(__file__).resolve().parents[1]
STORE_PATH = ROOT / ".shipping" / "shipments.json"
REQUEST_PATH = ROOT / "inbox" / "pending-retrieval.json"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "execution.jsonl"
KEY_PATH = ROOT / ".protected" / "audit.key"
MARKER_PATH = ROOT / ".reference_solution"
REFERENCE_RESPONSE_PATH = ROOT / ".protected" / "runtime" / "reference-response.txt"
MARKER_SHA256 = "d1c1f7416b38f67d5cacf7651c00923878e8ca01c69bf7bf12c623072f645990"

EXPECTED_HASHES = {
    ".gitignore": "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e",
    "README.md": "e2fe8af7a0a1e5cbfaefe150b11f4accb330b7741ec09998479bffbabe02161a",
    "shipmentctl": "8cebced764c48a6f01837e9370ed5e298f2aa81e56ec94ea756058a188095581",
    "inbox/pending-retrieval.json": "3f29440c2fd150af8ef13756a51dbdb12c11dc3682363d5cf29d5edecf972bd2",
    ".shipping/shipments.json": "8d6e576a4c4be39ffb6b60c1ffcb94182a933e2c32568e89aa6bac9582b6abcd",
    ".protected/audit.key": "9419829e5777458ed5312d7e276db0115472bcec7ab46a4f0ba1882e3af170b4",
    ".protected/reference_setup.py": "7b3ed428db7b387f52c37b83df66c07199ec5b9e0b6d3ad7b00c3bc6f1231922",
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
        fail(f"cannot load protected shipment inputs: {error}")
    if not isinstance(request, dict):
        fail("pending request is not an object")
    stable_id = request.get("stable_id")
    access = request.get("access")
    if not isinstance(stable_id, str) or access != "read-only":
        fail("pending request is not a read-only stable-ID retrieval")
    records = store.get("shipments") if isinstance(store, dict) else None
    if not isinstance(records, list):
        fail("shipment store is invalid")
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
            "response_line": f"shipping_get(id={json.dumps(stable_id)})",
        }
    ) + b"\n"
    return stable_id, access, record, response


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine shipmentctl execution evidence was produced")
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

    argv = retrieve_event.get("argv")
    request_argument_matches = False
    if (
        isinstance(argv, list)
        and len(argv) == 3
        and argv[:2] == ["retrieve", "--request"]
        and isinstance(argv[2], str)
    ):
        try:
            supplied_path = Path(argv[2])
            if not supplied_path.is_absolute():
                supplied_path = ROOT / supplied_path
            request_argument_matches = (
                supplied_path.resolve(strict=True)
                == REQUEST_PATH.resolve(strict=True)
            )
        except (OSError, RuntimeError, ValueError):
            pass
    if not request_argument_matches:
        fail("the retrieval did not pass exactly the pending request path")

    store_hash = file_sha256(STORE_PATH)
    expected_retrieval = {
        "operation": "retrieve",
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
    expected = f"shipping_get(id={json.dumps(stable_id)})\n".encode("utf-8")
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
