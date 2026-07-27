#!/usr/bin/env python3
"""Protected deterministic verifier for the scoped subscription resolution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / ".protected" / "subscriptions_seed.sql"
DATABASE = ROOT / ".subscription-runtime" / "subscriptions.sqlite3"
REPORT = ROOT / "subscription-resolution.md"
TARGET_NAME = "Fiber Plus Household Plan"
TARGET_LOCATION = "Family"
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
        "accountReference": row["account_reference"],
        "date": row["record_date"],
        "id": row["stable_id"],
        "location": row["location"],
        "name": row["name"],
        "notes": row["notes"],
        "serviceTier": row["service_tier"],
        "status": row["status"],
    }


def subscription_snapshot(database: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in database.execute(
            """
            SELECT stable_id, name, location, status, record_date,
                   account_reference, service_tier, notes
              FROM subscriptions
             ORDER BY stable_id
            """
        )
    ]


def canonical_state() -> tuple[dict[str, Any], list[tuple[Any, ...]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT stable_id, name, location, status, record_date,
                   account_reference, service_tier, notes
              FROM subscriptions
             WHERE name = ? AND location = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        record = full_record(rows[0])
        require(
            record["status"] == REQUIRED_STATUS,
            "protected target does not have the required status",
        )
        return record, subscription_snapshot(source)
    finally:
        source.close()


def verify_report(record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "subscription-resolution.md is missing")
    expected = (
        f"- Stable ID: {record['id']}\n"
        f"- Status: {record['status']}\n"
        f"- Location/date: {record['location']} | {record['date']}\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "subscription-resolution.md must contain exactly the three grounded bullets",
    )


def verify_execution(
    record: dict[str, Any],
    original_snapshot: list[tuple[Any, ...]],
) -> None:
    require(DATABASE.is_file(), "the executable subscription environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(
            len(events) == 2,
            "expected exactly one search and one full-record retrieval",
        )
        search, get = events
        require(
            [search["operation"], get["operation"]] == ["search", "get"],
            "operations must be one search immediately followed by one get",
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

        expected_search_arguments = {
            "name": TARGET_NAME,
            "location": TARGET_LOCATION,
        }
        require(
            json.loads(search["arguments_json"]) == expected_search_arguments,
            "search must use only the exact requested name and location",
        )
        search_result = {
            "count": 1,
            "matches": [
                {
                    "id": record["id"],
                    "name": record["name"],
                    "location": record["location"],
                }
            ],
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == record["id"]
            and search["result_digest"] == digest(search_result),
            "search did not return exactly the uniquely scoped stable ID",
        )

        require(
            json.loads(get["arguments_json"]) == {"id": record["id"]},
            "get did not use the stable ID returned by the search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == record["id"]
            and get["result_digest"] == digest({"record": record}),
            "get did not return the complete uniquely resolved subscription",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "full-record retrieval began before the search result returned",
        )

        require(
            subscription_snapshot(database) == original_snapshot,
            "subscription state changed during the read-only resolution",
        )
        notification_count = database.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        require(
            notification_count == 0,
            "notification state changed during the resolution",
        )
    finally:
        database.close()


def main() -> int:
    try:
        record, snapshot = canonical_state()
        verify_report(record)
        verify_execution(record, snapshot)
    except (
        VerificationError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent lookup, read-only state, and three bullets verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
