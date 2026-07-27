#!/usr/bin/env python3
"""Protected deterministic verifier for the reservation audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".reservation-runtime" / "reservations.sqlite3"
SEED = ROOT / ".protected" / "reservation_seed.sql"
REPORT = ROOT / "reservation-audit.json"
TARGET_NAME = "Maple Hall Planning Retreat"
TARGET_LOCATION = "Austin"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def detail_record(row: sqlite3.Row) -> dict[str, str]:
    return {
        "id": row["id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "date": row["event_date"],
    }


def snapshot(database: sqlite3.Connection) -> dict[str, list[tuple[Any, ...]]]:
    return {
        "reservations": [
            tuple(row)
            for row in database.execute(
                """
                SELECT id, name, location, status, event_date,
                       planner_contact, internal_note
                  FROM reservations
                 ORDER BY id
                """
            )
        ],
        "profiles": [
            tuple(row)
            for row in database.execute(
                """
                SELECT profile_name, preference
                  FROM saved_profiles
                 ORDER BY profile_name
                """
            )
        ],
        "availability": [
            tuple(row)
            for row in database.execute(
                """
                SELECT reservation_id, service_date, available
                  FROM availability
                 ORDER BY reservation_id, service_date
                """
            )
        ],
        "notifications": [
            tuple(row)
            for row in database.execute(
                """
                SELECT sequence, reservation_id, message
                  FROM notifications
                 ORDER BY sequence
                """
            )
        ],
    }


def canonical_target() -> tuple[dict[str, str], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT id, name, location, status, event_date
              FROM reservations
             WHERE name = ? AND location = ?
             ORDER BY id
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        target = detail_record(rows[0])
        require(target["status"] == "active", "protected target must be active")
        return target, snapshot(source)
    finally:
        source.close()


def verify_report(target: dict[str, str]) -> None:
    require(REPORT.is_file(), "reservation-audit.json is missing")
    expected = {
        "id": target["id"],
        "name": target["name"],
        "location": target["location"],
        "status": target["status"],
        "date": target["date"],
        "plannerContact": "unknown",
    }
    expected_text = json.dumps(expected, ensure_ascii=False, indent=2) + "\n"
    require(
        REPORT.read_text(encoding="utf-8") == expected_text,
        "reservation-audit.json is not the exact detail-grounded audit",
    )


def verify_execution(
    target: dict[str, str],
    expected_snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the reservation executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM execution_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 2, "expected exactly one search and one get")
        search, get = events
        require(
            [search["operation"], get["operation"]] == ["search", "get"],
            "the exact search must be followed by the dependent get",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                for event in events
            ),
            "a forbidden, failed, or incomplete reservation operation was recorded",
        )

        search_arguments = json.loads(search["arguments_json"])
        require(
            search_arguments == {
                "location": TARGET_LOCATION,
                "name": TARGET_NAME,
            },
            "search did not use the exact requested name and location",
        )
        search_result = {
            "matches": [
                {
                    "id": target["id"],
                    "name": target["name"],
                    "location": target["location"],
                    "status": target["status"],
                }
            ]
        }
        require(
            search["result_count"] == 1
            and search["sole_id"] == target["id"]
            and search["result_digest"] == digest(search_result),
            "search did not return the unique active requested reservation",
        )

        get_arguments = json.loads(get["arguments_json"])
        require(
            get_arguments == {"id": search["sole_id"]},
            "get did not use only the stable ID returned by search",
        )
        require(
            get["result_count"] == 1
            and get["sole_id"] == target["id"]
            and get["result_digest"] == digest({"record": target}),
            "get did not return the required full record",
        )
        require(
            int(search["finished_ns"]) < int(get["started_ns"]),
            "get started before the search result was available",
        )
        require(
            int(search["parent_pid"]) != int(get["parent_pid"]),
            "search and get were combined in one shell action",
        )
        require(
            snapshot(database) == expected_snapshot,
            "reservation or notification state changed during the read-only audit",
        )
    finally:
        database.close()


def main() -> int:
    try:
        target, expected_snapshot = canonical_target()
        verify_report(target)
        verify_execution(target, expected_snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact dependent lookup, grounded audit, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
