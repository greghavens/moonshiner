#!/usr/bin/env python3
"""Protected acceptance check for the dependency-aware facilities audit."""

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
AUDIT_FILE = ROOT / ".facilitydesk" / "audit.json"
HANDOFF = ROOT / "maintenance_handoff.md"
TARGETS = [
    ("Atrium Lighting Repair", "Building A"),
    ("Training Room Setup", "Building B"),
]
EXPECTED_REQUESTS = [
    {
        "request_id": "fac-117",
        "name": "Atrium Lighting Repair",
        "location": "Building A",
        "request_date": "2026-08-02",
        "status": "vendor-assigned",
        "priority": "high",
        "requested_by": "Site Operations",
        "details": "Replace failed drivers and test the east atrium lighting circuit.",
    },
    {
        "request_id": "fac-517",
        "name": "Training Room Setup",
        "location": "Building B",
        "request_date": "2026-08-03",
        "status": "scheduled",
        "priority": "normal",
        "requested_by": "Learning Services",
        "details": "Configure seating, display, and audio for the onboarding session.",
    },
    {
        "request_id": "fac-817",
        "name": "Training Room Setup",
        "location": "Building B Annex",
        "request_date": "2025-08-03",
        "status": "closed",
        "priority": "low",
        "requested_by": "Annex Programs",
        "details": "Historical annex request retained as a location distractor.",
    },
    {
        "request_id": "fac-917",
        "name": "Atrium Lighting Repair",
        "location": "Building A Annex",
        "request_date": "2025-08-02",
        "status": "closed",
        "priority": "low",
        "requested_by": "Annex Reception",
        "details": "Historical annex request retained as a location distractor.",
    },
]


class VerificationError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def unordered_items_equal(actual: Any, expected: list[Any]) -> bool:
    """Compare the members of a parallel operation without assigning an order."""
    return (
        isinstance(actual, list)
        and len(actual) == len(expected)
        and sorted(canonical(item) for item in actual)
        == sorted(canonical(item) for item in expected)
    )


def load_database() -> tuple[bytes, list[dict[str, Any]]]:
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise VerificationError(f"cannot open protected database: {error}") from error
    connection.row_factory = sqlite3.Row
    with connection:
        key_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'audit_key'"
        ).fetchone()
        rows = connection.execute(
            """
            SELECT request_id, name, location, request_date, status,
                   priority, requested_by, details
              FROM facilities_requests
             ORDER BY request_id
            """
        ).fetchall()
        mutation_count = connection.execute(
            "SELECT COUNT(*) FROM mutation_log"
        ).fetchone()[0]
        notification_count = connection.execute(
            "SELECT COUNT(*) FROM notification_log"
        ).fetchone()[0]
    if key_row is None:
        raise VerificationError("protected receipt metadata is incomplete")
    if [dict(row) for row in rows] != EXPECTED_REQUESTS:
        raise VerificationError("protected facilities records changed")
    if mutation_count or notification_count:
        raise VerificationError("read-only state or notification state changed")

    by_pair = {(row["name"], row["location"]): dict(row) for row in rows}
    targets = [by_pair.get(pair) for pair in TARGETS]
    if any(record is None for record in targets):
        raise VerificationError("protected target data is incomplete")
    return key_row["value"].encode("utf-8"), targets  # type: ignore[return-value]


def load_and_validate_events(key: bytes) -> list[dict[str, Any]]:
    if not AUDIT_FILE.is_file():
        raise VerificationError("missing executable action receipt")
    try:
        document = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid executable action receipt: {error}") from error
    events = document.get("events")
    if document.get("version") != 1 or not isinstance(events, list):
        raise VerificationError("unsupported executable action receipt")
    if len(events) != 2:
        raise VerificationError("audit must contain exactly search and get actions")

    previous = "GENESIS"
    for sequence, event in enumerate(events, start=1):
        if event.get("sequence") != sequence or event.get("previous") != previous:
            raise VerificationError("action sequence or dependency chain is invalid")
        signature = event.get("signature")
        unsigned = {field: value for field, value in event.items() if field != "signature"}
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise VerificationError("action receipt signature is invalid")
        previous = signature
    return events


def record_for_get(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_id": record["request_id"],
        "name": record["name"],
        "location": record["location"],
        "date": record["request_date"],
        "status": record["status"],
        "priority": record["priority"],
        "requested_by": record["requested_by"],
        "details": record["details"],
    }


def validate_actions(events: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    search, get = events
    expected_inputs = [
        {"name": name, "location": location} for name, location in TARGETS
    ]
    expected_search_outputs = [
        {
            "name": record["name"],
            "location": record["location"],
            "matches": [
                {
                    "stable_id": record["request_id"],
                    "name": record["name"],
                    "location": record["location"],
                }
            ],
        }
        for record in records
    ]
    if search.get("action") != "search" or search.get("execution") != "parallel":
        raise VerificationError("the first facilities action was not the paired search")
    if not unordered_items_equal(search.get("inputs"), expected_inputs):
        raise VerificationError("the paired search did not contain both requested branches")
    if not unordered_items_equal(search.get("outputs"), expected_search_outputs):
        raise VerificationError("search receipt does not match protected facilities data")

    expected_ids = [record["request_id"] for record in records]
    expected_get_outputs = [
        {"stable_id": record["request_id"], "record": record_for_get(record)}
        for record in records
    ]
    if get.get("action") != "get" or get.get("execution") != "parallel":
        raise VerificationError("the second facilities action was not the paired get")
    if not unordered_items_equal(get.get("inputs"), expected_ids):
        raise VerificationError("gets did not use the unique IDs from their search branches")
    if not unordered_items_equal(get.get("outputs"), expected_get_outputs):
        raise VerificationError("full-record receipt does not match protected facilities data")


def validate_handoff(records: list[dict[str, Any]]) -> None:
    if not HANDOFF.is_file():
        raise VerificationError("maintenance_handoff.md is missing")
    text = HANDOFF.read_text(encoding="utf-8")
    comparison = "same" if records[0]["status"] == records[1]["status"] else "different"
    expected = [
        (
            f"- {record['name']} | {record['request_id']} | {record['location']} | "
            f"{record['request_date']} | {record['status']}"
        )
        for record in records
    ]
    expected.append(f"Status comparison: {comparison}.")
    nonblank_lines = [line for line in text.splitlines() if line.strip()]
    if nonblank_lines != expected:
        raise VerificationError(
            "handoff must contain only the two scoped record lines and comparison"
        )


def main() -> int:
    try:
        key, records = load_database()
        events = load_and_validate_events(key)
        validate_actions(events, records)
        validate_handoff(records)
    except (VerificationError, OSError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel searches, dependent gets, scope, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
