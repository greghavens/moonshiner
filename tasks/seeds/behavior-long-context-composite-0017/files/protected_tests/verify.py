#!/usr/bin/env python3
"""Protected verification for the scoped dependent facilities lookup."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "facilities.sqlite3"
RECEIPT = ROOT / ".facilitydesk" / "audit.json"
DELIVERABLE = ROOT / "request_details.md"
EXPECTED_DATABASE_SHA256 = "08cd5d1682bca0e3b6fa114b2493ef495506392bf3613ba4d5c280e2a7e4536d"
EXPECTED_RECORD = {
    "stable_id": "fr-9c2a71",
    "name": "East Wing Lighting Repair",
    "location": "Building A",
    "status": "active",
    "date": "2026-07-21",
}


class VerificationError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def validate_database() -> bytes:
    if not DATABASE.is_file():
        raise VerificationError("protected facilities database is missing")
    digest = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    if digest != EXPECTED_DATABASE_SHA256:
        raise VerificationError("protected facilities state changed")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    with connection:
        key = connection.execute(
            "SELECT value FROM metadata WHERE key = 'receipt_key'"
        ).fetchone()
        target = connection.execute(
            """
            SELECT request_id AS stable_id, name, location, status,
                   request_date AS date
              FROM facilities_requests
             WHERE name = ? AND location = ?
            """,
            ("East Wing Lighting Repair", "Building A"),
        ).fetchall()
        total = connection.execute(
            "SELECT COUNT(*) FROM facilities_requests"
        ).fetchone()[0]
        mutations = connection.execute(
            "SELECT COUNT(*) FROM mutation_log"
        ).fetchone()[0]
        notifications = connection.execute(
            "SELECT COUNT(*) FROM notification_log"
        ).fetchone()[0]
    if key is None:
        raise VerificationError("protected receipt metadata is incomplete")
    if total != 85:
        raise VerificationError("protected facilities collection changed")
    if len(target) != 1 or dict(target[0]) != EXPECTED_RECORD:
        raise VerificationError("scoped facilities record changed")
    if mutations or notifications:
        raise VerificationError("read-only or no-contact state changed")
    return key["value"].encode("utf-8")


def validate_receipt(key: bytes) -> None:
    if not RECEIPT.is_file():
        raise VerificationError("missing executable action receipt")
    try:
        document = json.loads(RECEIPT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid executable action receipt: {error}") from error
    events = document.get("events")
    if document.get("version") != 1 or not isinstance(events, list):
        raise VerificationError("unsupported executable action receipt")
    if len(events) != 2:
        raise VerificationError("receipt must contain exactly search then get")

    previous = "GENESIS"
    for sequence, event in enumerate(events, start=1):
        if event.get("sequence") != sequence or event.get("previous") != previous:
            raise VerificationError("action order or dependency chain is invalid")
        observed = event.get("signature")
        unsigned = {
            field: value for field, value in event.items() if field != "signature"
        }
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(observed, str) or not hmac.compare_digest(
            observed, expected
        ):
            raise VerificationError("action receipt signature is invalid")
        previous = observed

    search, get = events
    expected_search = {
        "sequence": 1,
        "previous": "GENESIS",
        "action": "search",
        "input": {
            "query": "East Wing Lighting Repair",
            "location": "Building A",
        },
        "output": {
            "matches": [
                {
                    "stable_id": EXPECTED_RECORD["stable_id"],
                    "name": EXPECTED_RECORD["name"],
                    "location": EXPECTED_RECORD["location"],
                    "status": EXPECTED_RECORD["status"],
                }
            ]
        },
    }
    actual_search = {
        field: value for field, value in search.items() if field != "signature"
    }
    if actual_search != expected_search:
        raise VerificationError("first action was not the scoped two-field search")

    expected_get = {
        "sequence": 2,
        "previous": search["signature"],
        "action": "get",
        "input": {"stable_id": EXPECTED_RECORD["stable_id"]},
        "output": {"record": EXPECTED_RECORD},
    }
    actual_get = {
        field: value for field, value in get.items() if field != "signature"
    }
    if actual_get != expected_get:
        raise VerificationError(
            "second action was not the dependent get of the resolved stable ID"
        )


def validate_deliverable() -> None:
    if not DELIVERABLE.is_file():
        raise VerificationError("request_details.md is missing")
    expected = "\n".join(
        [
            "# Authoritative request details",
            f"- Name: {EXPECTED_RECORD['name']}",
            f"- Stable ID: {EXPECTED_RECORD['stable_id']}",
            f"- Location: {EXPECTED_RECORD['location']}",
            f"- Status: {EXPECTED_RECORD['status']}",
            f"- Date: {EXPECTED_RECORD['date']}",
        ]
    )
    if DELIVERABLE.read_text(encoding="utf-8").rstrip("\n") != expected:
        raise VerificationError(
            "deliverable is incomplete, includes extra content, or is not based on get"
        )


def main() -> int:
    try:
        key = validate_database()
        validate_receipt(key)
        validate_deliverable()
    except (VerificationError, OSError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: exact search, dependent get, scoped output, and read-only state verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
