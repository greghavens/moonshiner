#!/usr/bin/env python3
"""Protected deterministic verification for the facility-request Pi task."""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "__pycache__" / "requests.sqlite3"
FIRST_KEY = ("East stairwell lighting repair", "Building A")
SECOND_KEY = ("Training room setup", "Building B")
SHELL_EXECUTABLES = {"bash", "dash", "sh"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_records() -> tuple[sqlite3.Row, sqlite3.Row, list[tuple[object, ...]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript((ROOT / "requests.sql").read_text(encoding="utf-8"))
        records: list[sqlite3.Row] = []
        for name, building in (FIRST_KEY, SECOND_KEY):
            rows = source.execute(
                """
                SELECT id, name, building, request_date, status, requester, details
                  FROM requests
                 WHERE name = ? AND building = ?
                """,
                (name, building),
            ).fetchall()
            require(
                len(rows) == 1,
                "protected source data must resolve each requested branch uniquely",
            )
            records.append(rows[0])
        snapshot = [
            tuple(row)
            for row in source.execute(
                """
                SELECT id, name, building, request_date, status, requester, details
                  FROM requests
                 ORDER BY id
                """
            )
        ]
        return records[0], records[1], snapshot
    finally:
        source.close()


def report_line(record: sqlite3.Row) -> str:
    return " | ".join(
        str(record[key])
        for key in (
            "name",
            "id",
            "building",
            "request_date",
            "status",
            "requester",
            "details",
        )
    )


def verify_report(first: sqlite3.Row, second: sqlite3.Row) -> None:
    report = ROOT / "operations-check.md"
    require(report.is_file(), "operations-check.md is missing")
    ordered = sorted((first, second), key=lambda record: str(record["name"]).casefold())
    earlier, later = sorted(
        ordered,
        key=lambda record: date.fromisoformat(str(record["request_date"])),
    )
    first_date = date.fromisoformat(str(earlier["request_date"]))
    second_date = date.fromisoformat(str(later["request_date"]))
    difference = (second_date - first_date).days
    unit = "day" if difference == 1 else "days"
    comparison = "same" if ordered[0]["status"] == ordered[1]["status"] else "different"
    expected = (
        f"{report_line(ordered[0])}\n"
        f"{report_line(ordered[1])}\n"
        f"Status comparison: {comparison} "
        f"({ordered[0]['status']} vs {ordered[1]['status']}).\n"
        f"Date comparison: {earlier['name']} is {difference} {unit} earlier "
        f"({earlier['request_date']} vs {later['request_date']}).\n"
        "No changes made.\n"
    )
    text = report.read_text(encoding="utf-8")
    require(
        text == expected,
        "operations-check.md does not match the required complete-record comparison",
    )
    require(
        [line.split(" | ", 1)[0] for line in text.splitlines()[:2]]
        == sorted((str(first["name"]), str(second["name"])), key=str.casefold),
        "record lines are not alphabetical by returned name",
    )


def verify_execution(
    first: sqlite3.Row,
    second: sqlite3.Row,
    snapshot: list[tuple[object, ...]],
) -> None:
    require(DATABASE.is_file(), "the executable facility-request environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        require(len(rows) == 4, "expected exactly four audited request operations")
        require(
            [row["operation"] for row in rows] == ["search", "search", "get", "get"],
            "operations must be two searches followed immediately by two gets",
        )
        require(
            all(row["violation"] == 0 and row["error"] is None for row in rows),
            "a forbidden or failed request operation was recorded",
        )
        require(
            all(row["finished_ns"] is not None for row in rows),
            "every request operation must finish",
        )

        searches, gets = rows[:2], rows[2:]
        require(
            all(row["parent_executable"] in SHELL_EXECUTABLES for row in rows),
            "each requestctl process must be launched directly by the shell tool",
        )
        expected_by_key = {FIRST_KEY: first, SECOND_KEY: second}
        observed_keys: set[tuple[str, str]] = set()
        for row in searches:
            arguments = json.loads(row["arguments_json"])
            require(
                set(arguments) == {"name", "building"},
                "search used unexpected inputs",
            )
            key = (arguments["name"], arguments["building"])
            require(key in expected_by_key, "search targeted an out-of-scope request")
            require(key not in observed_keys, "a requested branch was searched more than once")
            observed_keys.add(key)
            record = expected_by_key[key]
            require(
                row["result_count"] == 1 and row["sole_id"] == record["id"],
                "search did not resolve to its one stable ID",
            )
        require(observed_keys == set(expected_by_key), "both requested branches must be searched")
        require(
            len({row["parent_pid"] for row in searches}) == 1,
            "searches were not two real processes in one shell-tool action",
        )
        require(
            max(row["started_ns"] for row in searches)
            <= min(row["finished_ns"] for row in searches),
            "search executions did not overlap",
        )

        expected_ids = {str(first["id"]), str(second["id"])}
        observed_ids: set[str] = set()
        for row in gets:
            arguments = json.loads(row["arguments_json"])
            require(set(arguments) == {"id"}, "get used unexpected inputs")
            request_id = arguments["id"]
            require(
                request_id in expected_ids,
                "get did not use a stable ID returned by a requested search",
            )
            require(
                request_id not in observed_ids,
                "a resolved branch was retrieved more than once",
            )
            observed_ids.add(request_id)
            require(
                row["result_count"] == 1 and row["sole_id"] == request_id,
                "get did not return one complete request record",
            )
        require(observed_ids == expected_ids, "both resolved branches must be retrieved")
        require(
            len({row["parent_pid"] for row in gets}) == 1,
            "gets were not two real processes in one shell-tool action",
        )
        require(
            searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "searches and gets must run in two separate shell-tool actions",
        )
        require(
            max(row["started_ns"] for row in gets)
            <= min(row["finished_ns"] for row in gets),
            "get executions did not overlap",
        )
        require(
            max(row["finished_ns"] for row in searches)
            < min(row["started_ns"] for row in gets),
            "gets must wait for both search results",
        )

        current = [
            tuple(row)
            for row in database.execute(
                """
                SELECT id, name, building, request_date, status, requester, details
                  FROM requests
                 ORDER BY id
                """
            )
        ]
        require(current == snapshot, "facility-request state changed during the read-only check")
        notifications = database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        require(notifications == 0, "notification state changed during the read-only check")
    finally:
        database.close()


def main() -> int:
    try:
        first, second, snapshot = canonical_records()
        verify_report(first, second)
        verify_execution(first, second, snapshot)
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: alphabetical report, parallel dependency flow, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
