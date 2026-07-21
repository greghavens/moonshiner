#!/usr/bin/env python3
"""Protected verifier for the account dependency-planning task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".account-runtime" / "accounts.sqlite3"
SEED = ROOT / ".protected" / "accounts_seed.sql"
TOOL = ROOT / "accountctl"
GUIDE = ROOT / "ACCOUNTS.md"
REPORT = ROOT / "account-audit.txt"
TARGETS = (
    ("Elm Learning Network", "Northeast Region"),
    ("Frost Community Health", "Mountain Region"),
)

# Fixed after the authored environment is finalized.
EXPECTED_SEED_SHA256 = "b8db076c28d1194351e149177581a9fe06e8fa9d83374ec43ce54bc707b8f4f9"
EXPECTED_TOOL_SHA256 = "dec826f87fcea0ec05e38978b70b5901dcdd58d941275477a7890e805101d8c9"
EXPECTED_GUIDE_SHA256 = "bee57ecb24a7b625800af2ef4d84f541038775d577bf8ec24e900a4342f9acd3"


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
        "date": row["record_date"],
        "id": row["id"],
        "internalNote": row["internal_note"],
        "location": row["location"],
        "name": row["name"],
        "owner": row["owner"],
        "serviceTier": row["service_tier"],
        "status": row["status"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256,
            "protected account seed changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "accountctl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "ACCOUNTS.md changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, status, record_date,
                       owner, service_tier, internal_note
                  FROM accounts
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "accounts": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, status, record_date,
                           owner, service_tier, internal_note
                      FROM accounts
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
                    SELECT location, service_family, review_slots
                      FROM availability
                     ORDER BY location, service_family
                    """
                )
            ],
            "notifications": [],
            "mutations": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "account-audit.txt is missing")
    earlier, later = sorted(records, key=lambda record: (record["date"], record["id"]))
    expected = (
        f"{earlier['date']} | {earlier['name']} | {earlier['location']} | "
        f"{earlier['id']} | {earlier['status']}\n"
        f"{later['date']} | {later['name']} | {later['location']} | "
        f"{later['id']} | {later['status']}\n"
        f"Status comparison: earlier is {earlier['status']}; "
        f"later is {later['status']}.\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "account-audit.txt is not the exact date-ordered status audit",
    )


def verify_stage(entries: list[sqlite3.Row], operation: str) -> None:
    require(len(entries) == 2, f"expected exactly two {operation} operations")
    require(len({int(entry["pid"]) for entry in entries}) == 2,
            f"the {operation} stage did not use two direct processes")
    require(len({int(entry["parent_pid"]) for entry in entries}) == 1,
            f"the {operation} processes did not share one shell action")
    starts = [entry["started_ns"] for entry in entries]
    finishes = [entry["finished_ns"] for entry in entries]
    require(all(isinstance(value, int) and value > 0 for value in starts + finishes),
            f"the {operation} timing evidence is invalid")
    require(all(start < finish for start, finish in zip(starts, finishes)),
            f"the {operation} timing interval is invalid")
    require(max(starts) < min(finishes),
            f"the two {operation} processes did not overlap")


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the account executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4,
                "the workflow must contain exactly two searches and two gets")
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
        verify_stage(searches, "search")
        verify_stage(gets, "get")
        require(searches[0]["parent_pid"] != gets[0]["parent_pid"],
                "searches and gets must occur in separate shell actions")
        require(
            max(int(event["finished_ns"]) for event in searches)
            < min(int(event["started_ns"]) for event in gets),
            "a get began before both search responses returned",
        )

        expected_by_pair = {
            (record["name"], record["location"]): record for record in records
        }
        searched_ids: dict[tuple[str, str], str] = {}
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(isinstance(arguments, dict)
                    and set(arguments) == {"location", "name"},
                    "a search was not an exact name-and-location search")
            pair = (arguments.get("name"), arguments.get("location"))
            require(pair in expected_by_pair and pair not in searched_ids,
                    "searches did not cover each requested branch exactly once")
            record = expected_by_pair[pair]
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
                "a search did not return its unique requested stable ID",
            )
            searched_ids[pair] = record["id"]
        require(set(searched_ids) == set(expected_by_pair),
                "both requested searches are required")

        ordered_ids = [searched_ids[pair] for pair in TARGETS]
        expected_by_id = {record["id"]: record for record in records}
        gets_by_id: dict[str, sqlite3.Row] = {}
        for event in gets:
            arguments = json.loads(event["arguments_json"])
            require(isinstance(arguments, dict) and set(arguments) == {"id"},
                    "a get must use only one returned stable ID")
            stable_id = arguments.get("id")
            require(stable_id in ordered_ids and stable_id not in gets_by_id,
                    "gets must use each unique searched ID exactly once")
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "a get did not return the required full account record",
            )
            gets_by_id[stable_id] = event
        require(set(gets_by_id) == set(ordered_ids),
                "both independently resolved branches must be retrieved")

        current = {
            "accounts": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, status, record_date,
                           owner, service_tier, internal_note
                      FROM accounts
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
                    SELECT location, service_family, review_slots
                      FROM availability
                     ORDER BY location, service_family
                    """
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, account_id, message FROM notifications ORDER BY sequence"
                )
            ],
            "mutations": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, operation, account_id, detail "
                    "FROM mutation_log ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot,
                "account, preference, availability, notification, or mutation state changed")
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
    print("PASS: dependency-gated parallel read-only account audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
