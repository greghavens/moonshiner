#!/usr/bin/env python3
"""Protected deterministic verification for the permit-queue Pi task."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "__pycache__" / "permit_queue.sqlite3"
FIRST_KEY = ("Pet License Renewal Application", "Aurora")
SECOND_KEY = ("Community Room Permit Application", "Lakewood")


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_records() -> tuple[sqlite3.Row, sqlite3.Row, list[tuple[object, ...]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript((ROOT / "applications.sql").read_text(encoding="utf-8"))
        records = []
        for name, location in (FIRST_KEY, SECOND_KEY):
            rows = source.execute(
                "SELECT id, name, location, status, date FROM applications WHERE name = ? AND location = ?",
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "protected source data must resolve each requested branch uniquely")
            records.append(rows[0])
        snapshot = [
            tuple(row)
            for row in source.execute(
                "SELECT id, name, location, status, date FROM applications ORDER BY id"
            )
        ]
        return records[0], records[1], snapshot
    finally:
        source.close()


def verify_brief(first: sqlite3.Row, second: sqlite3.Row) -> None:
    brief = ROOT / "brief.md"
    require(brief.is_file(), "brief.md is missing")
    expected = (
        "## First record\n"
        f"Name: {first['name']}\n"
        f"Location: {first['location']}\n"
        f"Status: {first['status']}\n\n"
        "## Second record\n"
        f"Name: {second['name']}\n"
        f"Location: {second['location']}\n"
        f"Status: {second['status']}\n\n"
        f"The first record is {first['status']}, while the second record is {second['status']}.\n"
    )
    require(brief.read_text(encoding="utf-8") == expected, "brief.md does not match the required exact shape and retrieved values")


def verify_execution(first: sqlite3.Row, second: sqlite3.Row, snapshot: list[tuple[object, ...]]) -> None:
    require(DATABASE.is_file(), "the executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        require(len(rows) == 4, "expected exactly four audited data operations")
        require([row["operation"] for row in rows] == ["search", "search", "get", "get"], "data operations must be two searches followed immediately by two gets")
        require(all(row["violation"] == 0 and row["error"] is None for row in rows), "a forbidden or failed operation was recorded")
        require(all(row["finished_ns"] is not None for row in rows), "every data operation must finish")

        searches, gets = rows[:2], rows[2:]
        expected_by_key = {FIRST_KEY: first, SECOND_KEY: second}
        observed_keys: set[tuple[str, str]] = set()
        for row in searches:
            arguments = json.loads(row["arguments_json"])
            require(set(arguments) == {"name", "location"}, "search used unexpected inputs")
            key = (arguments["name"], arguments["location"])
            require(key in expected_by_key, "search did not target one of the requested records")
            require(key not in observed_keys, "a requested branch was searched more than once")
            observed_keys.add(key)
            record = expected_by_key[key]
            require(row["result_count"] == 1 and row["sole_id"] == record["id"], "search did not resolve to its one stable ID")
        require(observed_keys == set(expected_by_key), "both requested branches must be searched")
        require(len({row["parent_pid"] for row in searches}) == 2, "searches were not separate executable-tool invocations")
        require(max(row["started_ns"] for row in searches) <= min(row["finished_ns"] for row in searches), "search executions did not overlap")

        expected_ids = {str(first["id"]), str(second["id"])}
        observed_ids: set[str] = set()
        for row in gets:
            arguments = json.loads(row["arguments_json"])
            require(set(arguments) == {"id"}, "get used unexpected inputs")
            application_id = arguments["id"]
            require(application_id in expected_ids, "get did not use a stable ID returned by a requested search")
            require(application_id not in observed_ids, "a resolved branch was retrieved more than once")
            observed_ids.add(application_id)
            require(row["result_count"] == 1 and row["sole_id"] == application_id, "get did not return one full record")
        require(observed_ids == expected_ids, "both resolved branches must be retrieved")
        require(len({row["parent_pid"] for row in gets}) == 2, "gets were not separate executable-tool invocations")
        require(max(row["started_ns"] for row in gets) <= min(row["finished_ns"] for row in gets), "get executions did not overlap")
        require(max(row["finished_ns"] for row in searches) < min(row["started_ns"] for row in gets), "gets must wait for both search results")

        current = [
            tuple(row)
            for row in database.execute(
                "SELECT id, name, location, status, date FROM applications ORDER BY id"
            )
        ]
        require(current == snapshot, "application state changed during the read-only audit")
        notifications = database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        require(notifications == 0, "notification state changed during the read-only audit")
    finally:
        database.close()


def main() -> int:
    try:
        first, second, snapshot = canonical_records()
        verify_brief(first, second)
        verify_execution(first, second, snapshot)
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact brief, dependency-gated parallel lookups, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
