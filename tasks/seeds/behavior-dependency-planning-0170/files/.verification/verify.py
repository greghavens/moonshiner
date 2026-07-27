#!/usr/bin/env python3
"""Deterministic acceptance checks for the paired record-operation task."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".records"
ERRORS: list[str] = []
RECEIPT_DOMAIN = "moonshiner-local-record-receipt-v1"


def load(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        ERRORS.append(f"missing {path.relative_to(ROOT)}")
        return None
    except json.JSONDecodeError as error:
        ERRORS.append(f"invalid JSON in {path.relative_to(ROOT)}: {error}")
        return None
    if not isinstance(value, dict):
        ERRORS.append(f"expected object in {path.relative_to(ROOT)}")
        return None
    return value


def expect(path: Path, wanted: dict[str, Any]) -> None:
    actual = load(path)
    if actual is not None and actual != wanted:
        ERRORS.append(
            f"wrong content in {path.relative_to(ROOT)}: "
            f"expected {wanted!r}, got {actual!r}"
        )


def expected_receipt(record_id: str, title: str, status: str, version: int) -> dict[str, Any]:
    body = {"id": record_id, "title": title, "status": status, "version": version}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(f"{RECEIPT_DOMAIN}\n{canonical}".encode()).hexdigest()
    return {**body, "receipt": digest}


def check_marker(stage: str, record_id: str, phase: str) -> None:
    path = DATA / "rendezvous" / stage / f"{record_id}.{phase}"
    wanted = f"{stage}:{record_id}:{phase}\n"
    try:
        actual = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        ERRORS.append(f"missing {path.relative_to(ROOT)}")
        return
    if actual != wanted:
        ERRORS.append(f"wrong rendezvous marker {path.relative_to(ROOT)}")


def main() -> int:
    expected_states = {
        "pro-270": {
            "id": "pro-270",
            "title": "Annual report edits",
            "status": "ready",
            "version": 13,
        },
        "pro-670": {
            "id": "pro-670",
            "title": "Workshop feedback summary",
            "status": "in-progress",
            "version": 8,
        },
    }
    starting = {
        "pro-270": ("Annual report edits", "in-progress", 12),
        "pro-670": ("Workshop feedback summary", "blocked", 7),
    }

    state_directory = DATA / "state"
    if state_directory.is_dir():
        state_files = sorted(path.name for path in state_directory.glob("*.json"))
        if state_files != ["pro-270.json", "pro-670.json"]:
            ERRORS.append(f"record set changed: {state_files!r}")
    else:
        ERRORS.append("missing .records/state")

    for record_id, state in expected_states.items():
        expect(state_directory / f"{record_id}.json", state)
        title, status, version = starting[record_id]
        expect(
            DATA / "receipts" / f"{record_id}.json",
            expected_receipt(record_id, title, status, version),
        )
        expect(
            DATA / "audit" / f"get-{record_id}.json",
            {
                "action": "get",
                "paired": True,
                "record_id": record_id,
                "returned_status": status,
                "version": version,
            },
        )

    transitions = {
        "pro-270": ("in-progress", "ready", 12),
        "pro-670": ("blocked", "in-progress", 7),
    }
    for record_id, (old_status, new_status, version) in transitions.items():
        expect(
            DATA / "audit" / f"transition-{record_id}.json",
            {
                "action": "transition",
                "from": old_status,
                "outcome": "updated",
                "paired": True,
                "record_id": record_id,
                "retrieved_version": version,
                "to": new_status,
            },
        )

    for stage in ("get", "transition"):
        for record_id in ("pro-270", "pro-670"):
            for phase in ("ready", "ack"):
                check_marker(stage, record_id, phase)

    audit_directory = DATA / "audit"
    if audit_directory.is_dir():
        audit_files = sorted(path.name for path in audit_directory.iterdir() if path.is_file())
        wanted_audits = [
            "get-pro-270.json",
            "get-pro-670.json",
            "transition-pro-270.json",
            "transition-pro-670.json",
        ]
        if audit_files != wanted_audits:
            ERRORS.append(f"unexpected or missing operations in audit: {audit_files!r}")
    else:
        ERRORS.append("missing .records/audit")

    if ERRORS:
        print("verification failed:")
        for error in ERRORS:
            print(f"- {error}")
        return 1
    print("verification passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
