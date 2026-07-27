#!/usr/bin/env python3
"""Protected verification for the dependent item-register workflow."""

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
SEED_PATH = PROTECTED / "items.json"
KEY_PATH = PROTECTED / "audit.key"
CLIENT_PATH = ROOT / "inventoryctl"
README_PATH = ROOT / "README.md"
HANDOFF_PATH = ROOT / "inventory_handoff.md"
GITIGNORE_PATH = ROOT / ".gitignore"
RUNTIME = ROOT / ".inventory-runtime"
DATABASE = RUNTIME / "register.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "item-record.md"

TARGET_NAME = "Recycled Packing Tape"
TARGET_LOCATION = "Warehouse C"
SEED_SHA256 = "49b3c55ac77773822510155008a5695a1096899bba97c21768eb3002eb1da484"
KEY_SHA256 = "c63f42bda8b9629e88b5011986a5dce19713c922416faaaa477655363212fbf2"
CLIENT_SHA256 = "deaf856d27198a16f5cbe4c6a8ab9ae5ea6c453f96dd26365b0dc453531444e2"
README_SHA256 = "281ec4fa01f4ff149f2cd4eb3ee0b2d33a1ae0bb1a1f6dfd596b3c2f40c8b092"
HANDOFF_SHA256 = "4f084e33807b8f103f50e97b6031c9d357c4688a17d312a8cb07e469fc3fd74f"
GITIGNORE_SHA256 = "9945262df667b43fd35047be5e2698389e8170274155e01d0acc68b7d2dcba78"

RECORD_FIELDS = (
    "id",
    "name",
    "location",
    "status",
    "date",
    "category",
    "quantity",
    "unit",
    "bin",
    "notes",
)
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


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected task file: {error}") from error


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def verify_static_files() -> None:
    require(file_sha256(SEED_PATH) == SEED_SHA256, "protected item seed changed")
    require(file_sha256(KEY_PATH) == KEY_SHA256, "protected evidence key changed")
    require(file_sha256(CLIENT_PATH) == CLIENT_SHA256, "inventory executable changed")
    require(file_sha256(README_PATH) == README_SHA256, "item-register README changed")
    require(file_sha256(HANDOFF_PATH) == HANDOFF_SHA256, "inventory handoff changed")
    require(file_sha256(GITIGNORE_PATH) == GITIGNORE_SHA256, "ignore rules changed")


def load_seed() -> list[dict[str, str]]:
    try:
        payload = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"protected item seed is unreadable: {error}") from error
    require(isinstance(payload, dict), "protected item seed is not an object")
    require(payload.get("schema_version") == 1, "protected item seed schema changed")
    records = payload.get("records")
    require(isinstance(records, list) and bool(records), "protected item seed is empty")

    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in records:
        require(isinstance(item, dict), "protected item record is not an object")
        require(set(item) == set(RECORD_FIELDS), "protected item record shape changed")
        record = {field: item[field] for field in RECORD_FIELDS}
        require(
            all(isinstance(value, str) for value in record.values()),
            "protected item record contains a non-string value",
        )
        require(record["id"] not in seen, "protected item seed has duplicate stable IDs")
        seen.add(record["id"])
        normalized.append(record)
    return normalized


def hamming_distance(left: str, right: str) -> int | None:
    if len(left) != len(right):
        return None
    return sum(a != b for a, b in zip(left, right))


def find_target(records: list[dict[str, str]]) -> dict[str, str]:
    matches = [
        record
        for record in records
        if record["name"] == TARGET_NAME
        and record["location"] == TARGET_LOCATION
    ]
    require(len(matches) == 1, "protected target is not unique")
    target = matches[0]
    require(target["status"] == "active", "protected target is not active")
    require(
        any(
            record["name"] == TARGET_NAME
            and record["location"] != TARGET_LOCATION
            for record in records
        ),
        "same-name location distractors are missing",
    )
    require(
        any(
            record["location"] == TARGET_LOCATION
            and record["name"] != TARGET_NAME
            and (
                TARGET_NAME in record["name"]
                or record["name"].casefold() == TARGET_NAME.casefold()
            )
            for record in records
        ),
        "near-name Warehouse C distractors are missing",
    )
    require(
        any(
            record["id"] != target["id"]
            and hamming_distance(record["id"], target["id"]) == 1
            for record in records
        ),
        "similar stable-ID distractors are missing",
    )
    return target


def expected_state(records: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "records": sorted(records, key=lambda record: record["id"]),
        "notifications": [],
    }


def verify_runtime_shape() -> None:
    require(RUNTIME.is_dir(), "item-register runtime is missing")
    entries = {path.name for path in RUNTIME.iterdir()}
    require(
        entries == {"register.sqlite3", "audit.jsonl", ".lock"},
        "item-register runtime contains missing or unexpected artifacts",
    )


def verify_database(records: list[dict[str, str]]) -> None:
    require(DATABASE.is_file(), "item-register database is missing")
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, name, location, status, date, category, quantity, unit,
                   bin, notes
            FROM items
            ORDER BY id
            """
        ).fetchall()
        actual = [
            {field: row[field] for field in RECORD_FIELDS}
            for row in rows
        ]
        notices = connection.execute(
            """
            SELECT sequence, item_id, message
            FROM notifications
            ORDER BY sequence
            """
        ).fetchall()
        marker_rows = connection.execute(
            "SELECT key, value FROM register_meta ORDER BY key"
        ).fetchall()
    except sqlite3.Error as error:
        raise VerificationError(f"item-register database is invalid: {error}") from error
    finally:
        if connection is not None:
            connection.close()

    require(
        actual == sorted(records, key=lambda record: record["id"]),
        "item records were changed or runtime state is not authoritative",
    )
    require(not notices, "a notification was sent")
    require(
        [(row["key"], row["value"]) for row in marker_rows]
        == [("seed_sha256", SEED_SHA256)],
        "item-register seed marker changed",
    )


def read_events() -> list[dict[str, Any]]:
    require(AUDIT.is_file(), "sealed execution evidence is missing")
    try:
        lines = AUDIT.read_text(encoding="utf-8").splitlines()
        key = KEY_PATH.read_bytes().strip()
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"cannot read sealed execution evidence: {error}") from error
    require(len(lines) == 2 and all(lines), "expected exactly two register operations")

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
        require(event["sequence"] == index, "register operation sequence is invalid")
        signature = event["signature"]
        require(isinstance(signature, str), f"evidence line {index} has no signature")
        unsigned = dict(event)
        del unsigned["signature"]
        expected_signature = hmac.new(
            key,
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
                "status": target["status"],
            }
        ],
    }
    get_result = {
        "record": {field: target[field] for field in RECORD_FIELDS}
    }
    state_sha256 = digest(expected_state(records))

    search, get = events
    require(search["action"] == "search", "first register operation was not search")
    require(
        search["request"]
        == {"name": TARGET_NAME, "location": TARGET_LOCATION},
        "search was not jointly constrained to the exact name and location",
    )
    require(search["success"] is True, "search did not succeed")
    require(
        search["result_sha256"] == digest(search_result),
        "search did not return exactly the unique active item summary",
    )

    require(get["action"] == "get", "second register operation was not full retrieval")
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
        require(event["seed_sha256"] == SEED_SHA256, "evidence seed digest changed")
        require(event["state_sha256"] == state_sha256, "item-register state was changed")


def verify_deliverable(target: dict[str, str]) -> None:
    require(DELIVERABLE.is_file(), "item-record.md is missing")
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VerificationError(f"item-record.md is unreadable: {error}") from error
    expected = (
        "| Field | Value |\n"
        "|---|---|\n"
        f"| ID | {target['id']} |\n"
        f"| name | {target['name']} |\n"
        f"| location | {target['location']} |\n"
        f"| status | {target['status']} |\n"
        f"| date | {target['date']} |\n"
    )
    require(
        actual == expected,
        "item-record.md does not match the retrieved fields and exact table format",
    )


def main() -> int:
    try:
        verify_static_files()
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
    print(
        "verification passed: exact scoped search, dependent get, unchanged state, exact table"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
