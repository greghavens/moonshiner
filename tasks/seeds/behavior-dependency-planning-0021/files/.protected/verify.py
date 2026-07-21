#!/usr/bin/env python3
"""Protected deterministic verification for the meeting-status Pi task."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / "__pycache__" / "meetings.sqlite3"
FIRST_KEY = ("Vendor Accessibility Check-In", "Remote - Meet")
SECOND_KEY = ("Facilities Safety Debrief", "Boston Office")


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_records() -> tuple[sqlite3.Row, sqlite3.Row, list[tuple[object, ...]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript((ROOT / "meetings.sql").read_text(encoding="utf-8"))
        records: list[sqlite3.Row] = []
        for name, location in (FIRST_KEY, SECOND_KEY):
            rows = source.execute(
                """
                SELECT id, name, location, meeting_date, status, owner, notes
                  FROM meetings
                 WHERE name = ? AND location = ?
                """,
                (name, location),
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
                SELECT id, name, location, meeting_date, status, owner, notes
                  FROM meetings
                 ORDER BY id
                """
            )
        ]
        return records[0], records[1], snapshot
    finally:
        source.close()


def verify_report(first: sqlite3.Row, second: sqlite3.Row) -> None:
    report = ROOT / "meeting-status.md"
    require(report.is_file(), "meeting-status.md is missing")
    text = report.read_text(encoding="utf-8")
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    require(len(words) < 75, "meeting-status.md must contain fewer than 75 words")
    comparison = "same" if first["status"] == second["status"] else "different"
    expected = (
        f"{first['name']} | {first['id']} | {first['status']}\n"
        f"{second['name']} | {second['id']} | {second['status']}\n"
        f"Status comparison: {comparison} ({first['status']} vs {second['status']}).\n"
        "No changes made.\n"
    )
    require(
        text == expected,
        "meeting-status.md does not match the required scoped four-line reply",
    )
    require(
        text.rstrip().endswith("No changes made."),
        "reply must end with the required exact sentence",
    )


def verify_execution(
    first: sqlite3.Row,
    second: sqlite3.Row,
    snapshot: list[tuple[object, ...]],
) -> None:
    require(DATABASE.is_file(), "the executable meeting environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute("SELECT * FROM audit_log ORDER BY sequence").fetchall()
        require(len(rows) == 4, "expected exactly four audited meeting operations")
        require(
            [row["operation"] for row in rows] == ["search", "search", "get", "get"],
            "operations must be two searches followed immediately by two gets",
        )
        require(
            all(row["violation"] == 0 and row["error"] is None for row in rows),
            "a forbidden or failed operation was recorded",
        )
        require(
            all(row["finished_ns"] is not None for row in rows),
            "every meeting operation must finish",
        )

        searches, gets = rows[:2], rows[2:]
        expected_by_key = {FIRST_KEY: first, SECOND_KEY: second}
        observed_keys: set[tuple[str, str]] = set()
        for row in searches:
            arguments = json.loads(row["arguments_json"])
            require(set(arguments) == {"name", "location"}, "search used unexpected inputs")
            key = (arguments["name"], arguments["location"])
            require(key in expected_by_key, "search targeted an out-of-scope meeting")
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
            meeting_id = arguments["id"]
            require(
                meeting_id in expected_ids,
                "get did not use a stable ID returned by a requested search",
            )
            require(
                meeting_id not in observed_ids,
                "a resolved branch was retrieved more than once",
            )
            observed_ids.add(meeting_id)
            require(
                row["result_count"] == 1 and row["sole_id"] == meeting_id,
                "get did not return one full meeting record",
            )
        require(observed_ids == expected_ids, "both resolved branches must be retrieved")
        require(
            len({row["parent_pid"] for row in gets}) == 1,
            "gets were not two real processes in one shell-tool action",
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
                SELECT id, name, location, meeting_date, status, owner, notes
                  FROM meetings
                 ORDER BY id
                """
            )
        ]
        require(current == snapshot, "meeting state changed during the read-only audit")
        notifications = database.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        require(notifications == 0, "notification state changed during the audit")
    finally:
        database.close()


def main() -> int:
    try:
        first, second, snapshot = canonical_records()
        verify_report(first, second)
        verify_execution(first, second, snapshot)
    except (VerificationError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: word-limited brief, parallel dependency flow, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
