#!/usr/bin/env python3
"""Protected deterministic verifier for the message-register task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".register-runtime" / "messages.sqlite3"
SEED = ROOT / ".protected" / "messages_seed.sql"
REPORT = ROOT / "resolved-message.md"
RECEIPT = ROOT / ".register-receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "a5cdf880125ab52f23d80f728ea85a2b4883865c4695dc6d008ac5b8e788419c"
)
TARGET_NAME = "Quarterly Donor Update"
TARGET_LOCATION = "Volunteers"
REQUIRED_STATUS = "active"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "audience": row["audience"],
        "body": row["body"],
        "date": row["message_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "status": row["status"],
    }


def state(database: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    return {
        "availability": [
            list(row)
            for row in database.execute(
                """
                SELECT location, message_date, channel_available
                  FROM availability
                 ORDER BY location, message_date
                """
            )
        ],
        "messages": [
            list(row)
            for row in database.execute(
                """
                SELECT id, name, location, message_date, status, audience, body
                  FROM messages
                 ORDER BY id
                """
            )
        ],
        "notifications": [
            list(row)
            for row in database.execute(
                "SELECT sequence, message_id, sent_at FROM notifications ORDER BY sequence"
            )
        ],
        "profiles": [
            list(row)
            for row in database.execute(
                "SELECT owner, delivery_profile FROM profiles ORDER BY owner"
            )
        ],
    }


def canonical_record_and_state() -> tuple[dict[str, Any], dict[str, list[list[Any]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT id, name, location, message_date, status, audience, body
              FROM messages
             WHERE name = ? AND location = ?
             ORDER BY id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target is not unique")
        record = full_record(rows[0])
        require(record["status"] == REQUIRED_STATUS, "protected target status is wrong")
        return record, state(source)
    finally:
        source.close()


def verify_report(record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "resolved-message.md is missing")
    expected = (
        f"- Stable ID: {record['id']}\n"
        f"- Status: {record['status']}\n"
        f"- Location/date: {record['location']} — {record['date']}\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "resolved-message.md is not the exact three-bullet grounded result",
    )


def expected_receipt(record: dict[str, Any], state_sha256: str) -> dict[str, Any]:
    search_result = {
        "matches": [
            {
                "id": record["id"],
                "location": record["location"],
                "name": record["name"],
            }
        ]
    }
    get_result = {"record": record}
    payload = {
        "operations": [
            {
                "arguments": {
                    "location": record["location"],
                    "name": record["name"],
                },
                "operation": "search",
                "result_count": 1,
                "result_digest": digest(search_result),
                "sole_id": record["id"],
            },
            {
                "arguments": {"id": record["id"]},
                "operation": "get",
                "result_count": 1,
                "result_digest": digest(get_result),
                "sole_id": record["id"],
            },
        ],
        "state_sha256": state_sha256,
        "version": 1,
    }
    receipt = dict(payload)
    receipt["signature"] = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_receipt(record: dict[str, Any], state_sha256: str) -> None:
    require(RECEIPT.is_file(), "the executable did not emit its audit receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the executable receipt must be an object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the receipt was not authentically generated by the executable",
    )
    require(
        receipt == expected_receipt(record, state_sha256),
        "the receipt does not prove the exact scoped dependent workflow",
    )


def verify_execution(
    record: dict[str, Any],
    canonical: dict[str, list[list[Any]]],
) -> None:
    require(DATABASE.is_file(), "the executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly two message-data operations")
        search, get = events
        require(
            [search["operation"], get["operation"]] == ["search", "get"],
            "the only operations must be search followed by get",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                and event["result_digest"] is not None
                for event in events
            ),
            "a forbidden, failed, incomplete, or ungrounded operation was recorded",
        )

        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments
            == {"location": TARGET_LOCATION, "name": TARGET_NAME},
            "search did not use both exact requested constraints",
        )
        expected_search = {
            "matches": [
                {
                    "id": record["id"],
                    "location": record["location"],
                    "name": record["name"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == record["id"]
            and search["result_digest"] == digest(expected_search),
            "search did not uniquely resolve the protected target",
        )

        get_arguments = json.loads(get["arguments_json"])
        require(
            get_arguments == {"id": search["sole_id"]},
            "get did not use only the stable ID returned by search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == record["id"]
            and get["result_digest"] == digest({"record": record}),
            "get did not retrieve the required complete record",
        )
        require(
            search["finished_ns"] < get["started_ns"],
            "get began before the search result returned",
        )
        require(
            search["parent_pid"] != get["parent_pid"],
            "search and get were not issued through separate dependent Bash calls",
        )

        current = state(database)
        require(current == canonical, "message register state was modified")
        state_sha256 = digest(current)
    finally:
        database.close()
    verify_receipt(record, state_sha256)


def main() -> int:
    try:
        record, canonical = canonical_record_and_state()
        verify_report(record)
        verify_execution(record, canonical)
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
