#!/usr/bin/env python3
"""Protected verification for the dependent message-registry workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ROOT / ".protected"
SEED_PATH = PROTECTED / "messages.json"
KEY_PATH = PROTECTED / "audit.key"
RUNTIME = ROOT / ".message-registry"
DATABASE = RUNTIME / "registry.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "message-record.json"

RECORD_FIELDS = (
    "id",
    "name",
    "location",
    "status",
    "date",
    "audience",
    "channel",
    "owner",
    "templateVersion",
    "notes",
)
OUTPUT_FIELDS = ("id", "name", "location", "status", "date")
EVENT_FIELDS = {
    "version",
    "sequence",
    "action",
    "request",
    "result_sha256",
    "seed_sha256",
    "state_sha256",
    "success",
    "signature",
}


class VerificationError(RuntimeError):
    """A deterministic verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_seed() -> list[dict[str, str]]:
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"protected seed is unreadable: {error}") from error
    require(payload.get("schema_version") == 1, "protected seed schema changed")
    records = payload.get("records")
    require(isinstance(records, list) and bool(records), "protected seed is empty")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        require(isinstance(item, dict), "protected seed record is not an object")
        require(set(item) == set(RECORD_FIELDS), "protected seed record shape changed")
        record = {field: item[field] for field in RECORD_FIELDS}
        require(
            all(isinstance(value, str) for value in record.values()),
            "protected seed record contains a non-string value",
        )
        require(record["id"] not in seen, "protected seed has duplicate stable IDs")
        seen.add(record["id"])
        normalized.append(record)
    return normalized


def find_target(records: list[dict[str, str]]) -> dict[str, str]:
    matches = [
        record
        for record in records
        if record["name"] == "Membership Renewal Reminder"
        and record["location"] == "Volunteers"
    ]
    require(len(matches) == 1, "protected target is not unique")
    require(
        any(
            record["name"] == "Membership Renewal Reminder"
            and record["location"] != "Volunteers"
            for record in records
        ),
        "protected exact-name location distractors are missing",
    )
    require(
        any(
            record["location"] == "Volunteers"
            and record["name"] != "Membership Renewal Reminder"
            and "Membership Renewal Reminder" in record["name"]
            for record in records
        ),
        "protected near-name Volunteers distractors are missing",
    )
    return matches[0]


def expected_state(records: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "records": sorted(records, key=lambda record: record["id"]),
        "notifications": [],
    }


def verify_database(records: list[dict[str, str]]) -> None:
    require(DATABASE.is_file(), "registry runtime database is missing")
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, name, location, status, date, audience, channel, owner,
                   template_version, notes
            FROM messages
            ORDER BY id
            """
        ).fetchall()
        actual = [
            {
                "id": row["id"],
                "name": row["name"],
                "location": row["location"],
                "status": row["status"],
                "date": row["date"],
                "audience": row["audience"],
                "channel": row["channel"],
                "owner": row["owner"],
                "templateVersion": row["template_version"],
                "notes": row["notes"],
            }
            for row in rows
        ]
        notifications = connection.execute(
            "SELECT sequence, message_id, text FROM notifications ORDER BY sequence"
        ).fetchall()
        meta = connection.execute(
            "SELECT value FROM registry_meta WHERE key = 'seed_sha256'"
        ).fetchone()
    except sqlite3.Error as error:
        raise VerificationError(f"registry runtime database is invalid: {error}") from error
    finally:
        if "connection" in locals():
            connection.close()
    require(
        actual == sorted(records, key=lambda record: record["id"]),
        "message records were changed or runtime state is not authoritative",
    )
    require(not notifications, "a notification was created")
    seed_sha256 = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    require(meta is not None and meta["value"] == seed_sha256, "runtime seed marker changed")


def read_events() -> list[dict[str, Any]]:
    require(AUDIT.is_file(), "sealed execution evidence is missing")
    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise VerificationError(f"cannot read execution evidence: {error}") from error
    require(len(lines) == 2 and all(lines), "expected exactly two registry operations")
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"execution evidence line {index} is invalid"
            ) from error
        require(isinstance(event, dict), f"evidence line {index} is not an object")
        require(set(event) == EVENT_FIELDS, f"evidence line {index} shape changed")
        require(event["version"] == 1, f"evidence line {index} version changed")
        require(event["sequence"] == index, "registry operation sequence is invalid")
        signature = event["signature"]
        require(isinstance(signature, str), f"evidence line {index} has no signature")
        unsigned = dict(event)
        del unsigned["signature"]
        expected_signature = hmac.new(
            KEY_PATH.read_bytes().strip(),
            canonical(unsigned),
            hashlib.sha256,
        ).hexdigest()
        require(
            hmac.compare_digest(signature, expected_signature),
            f"evidence line {index} is not authentic",
        )
        events.append(event)
    return events


def verify_events(
    events: list[dict[str, Any]],
    records: list[dict[str, str]],
    target: dict[str, str],
) -> None:
    search_result = {
        "match_count": 1,
        "matches": [
            {
                "id": target["id"],
                "name": target["name"],
                "location": target["location"],
            }
        ],
    }
    get_result = {"record": {field: target[field] for field in RECORD_FIELDS}}
    seed_sha256 = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    state_sha256 = digest(expected_state(records))

    search, get = events
    require(search["action"] == "search", "first registry operation was not search")
    require(
        search["request"]
        == {
            "name": "Membership Renewal Reminder",
            "location": "Volunteers",
        },
        "search was not jointly constrained to the exact requested name and location",
    )
    require(search["success"] is True, "search did not succeed")
    require(
        search["result_sha256"] == digest(search_result),
        "search did not resolve exactly the unique qualifying summary",
    )

    require(get["action"] == "get", "second registry operation was not full retrieval")
    require(
        get["request"] == {"id": search_result["matches"][0]["id"]},
        "full retrieval did not use the stable ID returned by search",
    )
    require(get["success"] is True, "full retrieval did not succeed")
    require(
        get["result_sha256"] == digest(get_result),
        "full retrieval did not return the authoritative complete record",
    )

    for event in events:
        require(event["seed_sha256"] == seed_sha256, "protected seed digest changed")
        require(event["state_sha256"] == state_sha256, "registry state was changed")


def verify_deliverable(target: dict[str, str]) -> None:
    require(DELIVERABLE.is_file(), "message-record.json is missing")
    try:
        raw = DELIVERABLE.read_text(encoding="utf-8")
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"message-record.json is invalid: {error}") from error
    require(isinstance(document, dict), "message-record.json must be one object")
    require(
        list(document) == list(OUTPUT_FIELDS),
        "output keys are missing, extra, or out of order",
    )
    expected = {field: target[field] for field in OUTPUT_FIELDS}
    require(document == expected, "output values do not match the full record")
    expected_text = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    require(
        raw == expected_text,
        "message-record.json must use two-space indentation and one final newline",
    )


def verify_runtime_shape() -> None:
    require(RUNTIME.is_dir(), "message registry runtime is missing")
    entries = {path.name for path in RUNTIME.iterdir()}
    require(
        entries <= {"registry.sqlite3", "audit.jsonl", ".lock"},
        "unexpected registry runtime artifacts exist",
    )


def main() -> int:
    try:
        records = load_seed()
        target = find_target(records)
        verify_runtime_shape()
        verify_database(records)
        events = read_events()
        verify_events(events, records, target)
        verify_deliverable(target)
    except VerificationError as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print("verification passed: scoped search, dependent get, unchanged state, ordered output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
