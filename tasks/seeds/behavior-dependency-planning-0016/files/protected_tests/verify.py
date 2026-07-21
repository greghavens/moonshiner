#!/usr/bin/env python3
"""Protected acceptance check for the staged claims audit."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "claims.sqlite3"
AUDIT_FILE = ROOT / ".claimdesk" / "audit.json"
BRIEF = ROOT / "audit_brief.md"
TARGETS = [
    ("Warehouse Theft Claim", "West Office"),
    ("Fleet Windshield Claim", "North Office"),
]


class VerificationError(Exception):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def load_database() -> tuple[bytes, list[dict[str, Any]]]:
    records = []
    with sqlite3.connect(DATABASE) as connection:
        connection.row_factory = sqlite3.Row
        key_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'audit_key'"
        ).fetchone()
        if key_row is None:
            raise VerificationError("protected database metadata is incomplete")
        for name, location in TARGETS:
            matches = connection.execute(
                """
                SELECT claim_id, name, location, status, loss_date,
                       amount_cents, adjuster, description
                  FROM claims
                 WHERE name = ? AND location = ?
                 ORDER BY claim_id
                """,
                (name, location),
            ).fetchall()
            if len(matches) != 1:
                raise VerificationError("protected target data is not unique")
            records.append(dict(matches[0]))
    return key_row["value"].encode("utf-8"), records


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
    if len(events) != 3:
        raise VerificationError("audit must contain exactly help, search, and get actions")

    previous = "GENESIS"
    for sequence, event in enumerate(events, start=1):
        if event.get("sequence") != sequence or event.get("previous") != previous:
            raise VerificationError("action sequence or dependency chain is invalid")
        signature = event.get("signature")
        unsigned = {key: value for key, value in event.items() if key != "signature"}
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            raise VerificationError("action receipt signature is invalid")
        previous = signature
    return events


def expected_search_outputs(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": record["name"],
            "location": record["location"],
            "matches": [
                {
                    "stable_id": record["claim_id"],
                    "name": record["name"],
                    "location": record["location"],
                }
            ],
        }
        for record in records
    ]


def validate_actions(events: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    help_event, search, get = events
    if help_event.get("action") != "help" or help_event.get("execution") != "single":
        raise VerificationError("top-level live help was not inspected first")
    if set(help_event) != {"sequence", "previous", "action", "execution", "signature"}:
        raise VerificationError("live-help receipt has an unexpected shape")

    expected_inputs = [
        {"name": name, "location": location} for name, location in TARGETS
    ]
    if search.get("action") != "search" or search.get("execution") != "parallel":
        raise VerificationError("the first action was not the paired search")
    search_inputs = search.get("inputs")
    search_outputs = search.get("outputs")
    if (
        not isinstance(search_inputs, list)
        or len(search_inputs) != len(expected_inputs)
        or any(item not in search_inputs for item in expected_inputs)
        or any(item not in expected_inputs for item in search_inputs)
    ):
        raise VerificationError("the paired search did not contain both requested branches")
    expected_outputs = expected_search_outputs(records)
    if not isinstance(search_outputs, list) or len(search_outputs) != len(search_inputs):
        raise VerificationError("search receipt does not match protected claim data")
    for search_input, search_output in zip(search_inputs, search_outputs):
        expected_index = expected_inputs.index(search_input)
        if search_output != expected_outputs[expected_index]:
            raise VerificationError("search receipt does not match protected claim data")

    expected_ids = [record["claim_id"] for record in records]
    if get.get("action") != "get" or get.get("execution") != "parallel":
        raise VerificationError("the second action was not the paired record retrieval")
    get_inputs = get.get("inputs")
    if (
        not isinstance(get_inputs, list)
        or len(get_inputs) != len(expected_ids)
        or any(stable_id not in get_inputs for stable_id in expected_ids)
        or any(stable_id not in expected_ids for stable_id in get_inputs)
    ):
        raise VerificationError("record retrieval did not follow the unique search IDs")
    records_by_id = {record["claim_id"]: record for record in records}
    expected_get_outputs = [
        {"stable_id": stable_id, "record": records_by_id[stable_id]}
        for stable_id in get_inputs
    ]
    if get.get("outputs") != expected_get_outputs:
        raise VerificationError("record retrieval did not follow the unique search IDs")


def validate_brief(records: list[dict[str, Any]]) -> None:
    if not BRIEF.is_file():
        raise VerificationError("audit_brief.md is missing")
    text = BRIEF.read_text(encoding="utf-8")
    lines = text.splitlines()
    comparison = "same" if records[0]["status"] == records[1]["status"] else "different"
    expected = [
        f"- {records[0]['name']} ({records[0]['location']}): {records[0]['status']}",
        (
            f"- {records[1]['name']} ({records[1]['location']}): "
            f"{records[1]['status']}; comparison: {comparison}"
        ),
    ]
    if lines != expected:
        raise VerificationError("brief must match the required two-bullet format and statuses")


def main() -> int:
    try:
        key, records = load_database()
        events = load_and_validate_events(key)
        validate_actions(events, records)
        validate_brief(records)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: live help, paired searches, dependent gets, constraints, and brief verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
