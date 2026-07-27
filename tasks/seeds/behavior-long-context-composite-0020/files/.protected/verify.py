#!/usr/bin/env python3
"""Protected deterministic verifier for the scoped fleet resolution."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / ".protected" / "fleet_seed.sql"
DATABASE = ROOT / ".fleet-runtime" / "fleet.sqlite3"
REPORT = ROOT / "vehicle-resolution.txt"
TARGET_NAME = "Shuttle 30"
TARGET_LOCATION = "Depot D"
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
        "assetTag": row["asset_tag"],
        "capacity": row["capacity"],
        "date": row["record_date"],
        "id": row["stable_id"],
        "location": row["location"],
        "name": row["name"],
        "notes": row["notes"],
        "status": row["status"],
        "vehicleClass": row["vehicle_class"],
    }


def vehicle_snapshot(database: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in database.execute(
            """
            SELECT stable_id, name, location, status, record_date,
                   asset_tag, vehicle_class, capacity, notes
              FROM vehicles
             ORDER BY stable_id
            """
        )
    ]


def table_snapshot(
    database: sqlite3.Connection,
    table: str,
    order_by: str,
) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in database.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    ]


def search_rows(
    database: sqlite3.Connection,
    name: str,
    location: str,
) -> list[sqlite3.Row]:
    return database.execute(
        """
        SELECT stable_id, name, location, status
          FROM vehicles
         WHERE location = ?
           AND instr(lower(name), lower(?)) > 0
         ORDER BY length(name) DESC, stable_id
        """,
        (location, name),
    ).fetchall()


def canonical_state() -> tuple[dict[str, Any], dict[str, list[tuple[Any, ...]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        rows = source.execute(
            """
            SELECT stable_id, name, location, status, record_date,
                   asset_tag, vehicle_class, capacity, notes
              FROM vehicles
             WHERE name = ? AND location = ? AND status = ?
             ORDER BY stable_id
            """,
            (TARGET_NAME, TARGET_LOCATION, REQUIRED_STATUS),
        ).fetchall()
        require(len(rows) == 1, "protected target must resolve uniquely")
        snapshots = {
            "vehicles": vehicle_snapshot(source),
            "availability": table_snapshot(
                source,
                "availability",
                "location, service_date",
            ),
            "profiles": table_snapshot(source, "profiles", "profile_id"),
            "notifications": table_snapshot(
                source,
                "notifications",
                "notification_id",
            ),
        }
        return full_record(rows[0]), snapshots
    finally:
        source.close()


def verify_report(record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "vehicle-resolution.txt is missing")
    expected = (
        f"Found: {record['name']} ({record['id']}) at {record['location']}; "
        f"status {record['status']}; date {record['date']}.\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "vehicle-resolution.txt must contain exactly the grounded Found sentence",
    )


def verify_execution(
    record: dict[str, Any],
    original: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the executable fleet environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(
            len(events) >= 3,
            "expected built-in help before one scoped search and one full-record retrieval",
        )
        help_events = [event for event in events if event["operation"] == "help"]
        searches = [event for event in events if event["operation"] == "search"]
        gets = [event for event in events if event["operation"] == "get"]
        require(
            len(searches) == 1 and len(gets) == 1,
            "fleet-data operations must be exactly one search and one get",
        )
        search, get = searches[0], gets[0]
        require(
            len(help_events) >= 1
            and all(event["operation"] in {"help", "search", "get"} for event in events)
            and any(event["sequence"] < search["sequence"] for event in help_events)
            and search["sequence"] < get["sequence"],
            "built-in help must precede the search, which must precede the get",
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
        require(
            all(
                event["pid"] > 0
                and event["parent_pid"] > 0
                and event["parent_start_ticks"] > 0
                for event in events
            ),
            "an executable invocation has invalid process identity evidence",
        )
        require(
            all(
                json.loads(event["arguments_json"]) == {}
                and event["result_count"] == 1
                and event["result_digest"] == digest({"inspected": True})
                for event in help_events
            ),
            "the executable's built-in help was not genuinely inspected",
        )

        expected_search_arguments = {
            "name": TARGET_NAME,
            "location": TARGET_LOCATION,
        }
        require(
            json.loads(search["arguments_json"]) == expected_search_arguments,
            "search must use only the exact requested name and location",
        )
        rows = search_rows(database, TARGET_NAME, TARGET_LOCATION)
        matches = [
            {
                "id": row["stable_id"],
                "location": row["location"],
                "name": row["name"],
                "status": row["status"],
            }
            for row in rows
        ]
        search_result = {"count": len(matches), "matches": matches}
        require(
            search["result_count"] == len(matches)
            and search["exact_count"] == 1
            and search["sole_id"] == record["id"]
            and search["result_digest"] == digest(search_result),
            "search did not establish the unique exact stable ID from the long result",
        )

        require(
            json.loads(get["arguments_json"]) == {"id": record["id"]},
            "get did not use the stable ID returned by the scoped search",
        )
        require(
            get["result_count"] == 1
            and get["exact_count"] == 1
            and get["sole_id"] == record["id"]
            and get["result_digest"] == digest({"record": record}),
            "get did not return the complete uniquely resolved vehicle",
        )
        require(
            (search["parent_pid"], search["parent_start_ticks"])
            != (get["parent_pid"], get["parent_start_ticks"]),
            "search and get must be separate shell-tool actions",
        )

        require(
            vehicle_snapshot(database) == original["vehicles"],
            "vehicle state changed during the read-only resolution",
        )
        require(
            table_snapshot(database, "availability", "location, service_date")
            == original["availability"],
            "availability state changed during the resolution",
        )
        require(
            table_snapshot(database, "profiles", "profile_id")
            == original["profiles"],
            "profile state changed during the resolution",
        )
        require(
            table_snapshot(database, "notifications", "notification_id")
            == original["notifications"],
            "notification state changed during the resolution",
        )
    finally:
        database.close()


def main() -> int:
    try:
        record, snapshots = canonical_state()
        verify_report(record)
        verify_execution(record, snapshots)
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
    print("PASS: scoped dependent lookup, distractor rejection, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
