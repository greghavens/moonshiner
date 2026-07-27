#!/usr/bin/env python3
"""Protected acceptance check for the dependent claims retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "claims.sqlite3"
AUDIT_FILE = ROOT / ".claimdesk" / "audit.json"
DELIVERABLE = ROOT / "claim_record.md"
TARGET_NAME = "Warehouse Theft Claim"
TARGET_LOCATION = "West Office"


class VerificationError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def load_database() -> tuple[bytes, list[dict[str, Any]]]:
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        key_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'receipt_key'"
        ).fetchone()
        if key_row is None:
            raise VerificationError("protected datastore metadata is incomplete")
        records = [
            dict(row)
            for row in connection.execute(
                """
                SELECT stable_id, name, location, status, date,
                       amount_cents, adjuster, description
                  FROM claims
                 WHERE name = ? AND location = ?
                 ORDER BY stable_id
                """,
                (TARGET_NAME, TARGET_LOCATION),
            ).fetchall()
        ]
        exact_elsewhere = connection.execute(
            """
            SELECT COUNT(*) FROM claims
             WHERE name = ? AND location <> ?
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchone()[0]
        near_at_target = connection.execute(
            """
            SELECT COUNT(*) FROM claims
             WHERE location = ? AND name <> ? AND name LIKE ?
            """,
            (TARGET_LOCATION, TARGET_NAME, f"%{TARGET_NAME}%"),
        ).fetchone()[0]
        total = connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    if len(records) != 1:
        raise VerificationError("protected target is not unique")
    if exact_elsewhere < 3 or near_at_target < 6 or total < 70:
        raise VerificationError("protected distractor corpus is incomplete")
    return key_row[0].encode("utf-8"), records


def load_events(key: bytes) -> list[dict[str, Any]]:
    if not AUDIT_FILE.is_file():
        raise VerificationError("missing claimdesk execution receipt")
    try:
        document = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid claimdesk execution receipt: {error}") from error
    events = document.get("events")
    if document.get("version") != 1 or not isinstance(events, list):
        raise VerificationError("unsupported claimdesk execution receipt")
    if len(events) != 3:
        raise VerificationError("expected exactly help, search, and get executions")
    previous = "GENESIS"
    for sequence, event in enumerate(events, start=1):
        if event.get("sequence") != sequence or event.get("previous") != previous:
            raise VerificationError("execution order or dependency chain is invalid")
        signature = event.get("signature")
        unsigned = {key_name: value for key_name, value in event.items()
                    if key_name != "signature"}
        expected = hmac.new(
            key, canonical(unsigned), hashlib.sha256
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            raise VerificationError("claimdesk execution receipt signature is invalid")
        previous = signature
    return events


def validate_actions(events: list[dict[str, Any]], record: dict[str, Any]) -> None:
    help_event, search_event, get_event = events
    if help_event.get("action") != "help":
        raise VerificationError("live top-level help was not inspected first")
    if set(help_event) != {"sequence", "previous", "action", "signature"}:
        raise VerificationError("help receipt has an unexpected shape")

    expected_input = {"name": TARGET_NAME, "location": TARGET_LOCATION}
    expected_match = {
        "stable_id": record["stable_id"],
        "name": record["name"],
        "location": record["location"],
    }
    expected_search_output = {
        "query": expected_input,
        "match_count": 1,
        "matches": [expected_match],
    }
    if (
        search_event.get("action") != "search"
        or search_event.get("input") != expected_input
        or search_event.get("output") != expected_search_output
    ):
        raise VerificationError(
            "search was not the required exact-name and exact-location execution"
        )

    expected_get_input = {"stable_id": record["stable_id"]}
    expected_get_output = {"stable_id": record["stable_id"], "record": record}
    if (
        get_event.get("action") != "get"
        or get_event.get("input") != expected_get_input
        or get_event.get("output") != expected_get_output
    ):
        raise VerificationError(
            "full retrieval did not depend on the unique stable ID from search"
        )


def validate_deliverable(record: dict[str, Any]) -> None:
    if not DELIVERABLE.is_file():
        raise VerificationError("claim_record.md is missing")
    lines = DELIVERABLE.read_text(encoding="utf-8").splitlines()
    expected = (
        f"{record['stable_id']} | {record['name']} | {record['location']} | "
        f"{record['status']} | {record['date']}"
    )
    if lines != [expected]:
        raise VerificationError(
            "claim_record.md must contain exactly the ordered retrieved fields"
        )


def main() -> int:
    try:
        key, records = load_database()
        events = load_events(key)
        validate_actions(events, records[0])
        validate_deliverable(records[0])
    except VerificationError as error:
        print(f"FAIL: {error}")
        return 1
    print("PASS: live help, narrow search, dependent full retrieval, and output verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
