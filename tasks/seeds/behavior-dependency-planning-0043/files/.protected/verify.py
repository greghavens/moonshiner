#!/usr/bin/env python3
"""Protected deterministic verifier for the lodging Pi-harness task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".lodging-runtime" / "lodging.sqlite3"
SEED = ROOT / ".protected" / "lodging_seed.sql"
TOOL = ROOT / "lodgectl"
GUIDE = ROOT / "LODGING.md"
REPORT = ROOT / "lodging-audit.md"
TARGETS = (
    ("Northstar Speaker Lodging", "Minneapolis"),
    ("Lakeside Volunteer Rooms", "Madison"),
)

# Fixed after the authored environment is finalized.
EXPECTED_SEED_SHA256 = "65dd02c4280c38fa9bbaaac9ea7368af540d51bd6c74860b578cb9347d99cc46"
EXPECTED_TOOL_SHA256 = "037c0756e0749209b172efd5f7be0c1b3098bf91a52a031fae0b41c07af3128c"
EXPECTED_GUIDE_SHA256 = "e54379cb03ec3d467fd7b420dc096d8d2dfea7378ab8b5889411c4ea8f43a898"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "arrivalDate": row["arrival_date"],
        "departureDate": row["departure_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "roomCount": row["room_count"],
        "status": row["status"],
        "venueNote": row["venue_note"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected lodging seed changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "lodgectl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "LODGING.md changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, status, arrival_date, departure_date,
                       room_count, venue_note
                  FROM reservations
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "reservations": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, status, arrival_date, departure_date,
                           room_count, venue_note
                      FROM reservations
                     ORDER BY id
                    """
                )
            ],
            "preferences": [
                tuple(row)
                for row in source.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT location, stay_date, rooms_available
                      FROM availability
                     ORDER BY location, stay_date
                    """
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "lodging-audit.md is missing")
    first, second = records
    relation = "match" if first["status"] == second["status"] else "differ"
    expected = (
        "## First record\n"
        f"{first['name']} ({first['location']}): {first['status']}\n"
        "\n"
        "## Second record\n"
        f"{second['name']} ({second['location']}): {second['status']}\n"
        "\n"
        f"{first['name']} is {first['status']} and {second['name']} is "
        f"{second['status']}, so the statuses {relation}.\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "lodging-audit.md does not have the exact ordered, grounded format",
    )


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the lodging executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4, "expected exactly four lodging data operations")
        require(
            [event["operation"] for event in events] == ["search", "search", "get", "get"],
            "operations must be two searches immediately followed by two gets",
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

        searches = events[:2]
        gets = events[2:]
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"location", "name"}, "search used extra inputs")
            key = (arguments["name"], arguments["location"])
            require(key in expected_by_key, "search targeted an unrequested reservation")
            require(key not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(key)
            record = expected_by_key[key]
            result = {
                "matches": [
                    {
                        "id": record["id"],
                        "location": record["location"],
                        "name": record["name"],
                    }
                ]
            }
            require(
                event["result_count"] == 1
                and event["sole_id"] == record["id"]
                and event["result_digest"] == digest(result),
                "search did not return the unique requested stable ID",
            )
        require(observed_keys == set(expected_by_key), "both exact searches are required")

        expected_by_id = {record["id"]: record for record in records}
        observed_ids: set[str] = set()
        for event in gets:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"id"}, "get used extra inputs")
            stable_id = arguments["id"]
            require(stable_id in expected_by_id, "get did not use a resolved target ID")
            require(stable_id not in observed_ids, "a resolved branch was retrieved twice")
            observed_ids.add(stable_id)
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "get did not return the required full reservation record",
            )
        require(observed_ids == set(expected_by_id), "both resolved branches are required")

        require(
            len({event["pid"] for event in searches}) == 2
            and len({event["parent_pid"] for event in searches}) == 1,
            "searches were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in searches)
            < min(event["finished_ns"] for event in searches),
            "the search processes did not overlap",
        )
        require(
            len({event["pid"] for event in gets}) == 2
            and len({event["parent_pid"] for event in gets}) == 1,
            "gets were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in gets)
            < min(event["finished_ns"] for event in gets),
            "the get processes did not overlap",
        )
        require(
            searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "searches and gets must occur in separate shell-tool actions",
        )
        require(
            max(event["finished_ns"] for event in searches)
            < min(event["started_ns"] for event in gets),
            "gets began before both search responses returned",
        )

        current = {
            "reservations": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, status, arrival_date, departure_date,
                           room_count, venue_note
                      FROM reservations
                     ORDER BY id
                    """
                )
            ],
            "preferences": [
                tuple(row)
                for row in database.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT location, stay_date, rooms_available
                      FROM availability
                     ORDER BY location, stay_date
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
        require(current == snapshot, "lodging state changed during the read-only audit")
    finally:
        database.close()


def main() -> int:
    try:
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact headed audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
