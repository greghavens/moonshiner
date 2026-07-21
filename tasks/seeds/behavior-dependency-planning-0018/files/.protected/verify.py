#!/usr/bin/env python3
"""Protected, deterministic verifier for the account-review task."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATA_DB = ROOT / "data" / "subscriptions.sqlite3"
JOURNAL_DB = ROOT / "var" / "subscription-executions.sqlite3"
REPORT = ROOT / "account-review.md"

TARGETS = (
    ("Family Fiber Subscription", "Residence Account"),
    ("Studio Tablet Subscription", "Creative Studio"),
)
FORBIDDEN = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}


class VerificationError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def expected_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{DATA_DB}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for name, location in TARGETS:
            rows = connection.execute(
                """
                SELECT stable_id, name, location, record_date, status,
                       account_reference, service_tier
                  FROM subscriptions
                 WHERE name = ? AND location = ?
                 ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            if len(rows) != 1:
                raise VerificationError("protected target data is not uniquely resolvable")
            row = rows[0]
            records.append(
                {
                    "accountReference": row["account_reference"],
                    "date": row["record_date"],
                    "id": row["stable_id"],
                    "location": row["location"],
                    "name": row["name"],
                    "serviceTier": row["service_tier"],
                    "status": row["status"],
                }
            )
    return records


def load_events() -> list[sqlite3.Row]:
    if not JOURNAL_DB.is_file():
        raise VerificationError("subscription tool execution journal is missing")
    connection = sqlite3.connect(f"file:{JOURNAL_DB}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            """
            SELECT event_id, operation, arguments_json, started_ns, ended_ns,
                   succeeded, result_digest, error_text
              FROM executions
             ORDER BY started_ns, event_id
            """
        ).fetchall()
    finally:
        connection.close()


def expected_search_result(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": 1,
        "matches": [
            {"id": record["id"], "location": record["location"], "name": record["name"]}
        ],
    }


def verify_execution(records: list[dict[str, Any]]) -> None:
    events = load_events()
    if len(events) != 4:
        raise VerificationError("expected exactly four subscription tool executions")
    if any(event["operation"] in FORBIDDEN for event in events):
        raise VerificationError("a prohibited subscription operation was executed")
    if any(event["succeeded"] != 1 or event["ended_ns"] is None for event in events):
        raise VerificationError("all four required operations must complete successfully")

    searches = [event for event in events if event["operation"] == "search"]
    gets = [event for event in events if event["operation"] == "get"]
    if len(searches) != 2 or len(gets) != 2:
        raise VerificationError("expected exactly two searches and two full-record retrievals")

    expected_searches = {
        canonical_json({"location": location, "name": name}): digest(
            expected_search_result(record)
        )
        for (name, location), record in zip(TARGETS, records, strict=True)
    }
    actual_searches = {
        event["arguments_json"]: event["result_digest"] for event in searches
    }
    if actual_searches != expected_searches:
        raise VerificationError("searches did not resolve the two requested name-location pairs")

    expected_gets = {
        canonical_json({"id": record["id"]}): digest({"record": record})
        for record in records
    }
    actual_gets = {event["arguments_json"]: event["result_digest"] for event in gets}
    if actual_gets != expected_gets:
        raise VerificationError("full-record retrievals do not match the uniquely resolved IDs")

    latest_search_start = max(event["started_ns"] for event in searches)
    earliest_search_end = min(event["ended_ns"] for event in searches)
    if latest_search_start >= earliest_search_end:
        raise VerificationError("the two searches were not executed concurrently")

    latest_search_end = max(event["ended_ns"] for event in searches)
    earliest_get_start = min(event["started_ns"] for event in gets)
    if earliest_get_start <= latest_search_end:
        raise VerificationError("retrieval began before both search responses completed")

    latest_get_start = max(event["started_ns"] for event in gets)
    earliest_get_end = min(event["ended_ns"] for event in gets)
    if latest_get_start >= earliest_get_end:
        raise VerificationError("the two full-record retrievals were not executed concurrently")


def markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def verify_report(records: list[dict[str, Any]]) -> None:
    if not REPORT.is_file():
        raise VerificationError("account-review.md is missing")
    text = REPORT.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    separator_indexes = [
        index
        for index, line in enumerate(lines)
        if "|" in line
        and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in markdown_cells(line)
        )
    ]
    if len(separator_indexes) != 1:
        raise VerificationError("report must contain exactly one two-row Markdown table")
    table_start = separator_indexes[0] - 1
    if table_start < 0 or table_start + 3 >= len(lines):
        raise VerificationError("the Markdown table is incomplete")
    table_lines = lines[table_start : table_start + 4]
    if markdown_cells(table_lines[0]) != ["Name", "ID", "Date", "Status"]:
        raise VerificationError("table columns are missing or out of order")
    separator = markdown_cells(table_lines[1])
    if len(separator) != 4 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        raise VerificationError("Markdown table separator is invalid")

    expected_rows = [
        [
            record["name"],
            record["id"] if record["id"] is not None else "unknown",
            record["date"] if record["date"] is not None else "unknown",
            record["status"] if record["status"] is not None else "unknown",
        ]
        for record in records
    ]
    actual_rows = [markdown_cells(line) for line in table_lines[2:]]
    expected_by_name = {row[0]: row for row in expected_rows}
    actual_by_name = {row[0]: row for row in actual_rows if len(row) == 4}
    if len(actual_by_name) != 2 or actual_by_name != expected_by_name:
        raise VerificationError("table values are not grounded in the retrieved full records")

    after_table = [line.strip() for line in lines[table_start + 4 :]]
    if len(after_table) != 1:
        raise VerificationError("add exactly one concise status-comparison sentence")
    sentence = after_table[0]
    statuses = [str(record["status"]) for record in records]
    folded_sentence = sentence.casefold()
    if not sentence.endswith((".", "!", "?")) or not all(
        status.casefold() in folded_sentence for status in statuses
    ):
        raise VerificationError("status comparison must name both retrieved status values")
    if statuses[0] != statuses[1] and not re.search(
        r"\b(differ|different|while|whereas|but|versus|vs\.?)\b", sentence, re.I
    ):
        raise VerificationError("status comparison must state that the values differ")
    if statuses[0] == statuses[1] and not re.search(
        r"\b(same|match|equal|both)\b", sentence, re.I
    ):
        raise VerificationError("status comparison must state that the values match")


def main() -> int:
    try:
        records = expected_records()
        verify_execution(records)
        verify_report(records)
    except (OSError, sqlite3.Error, VerificationError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("PASS: concurrent searches, dependent concurrent gets, and grounded report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
