#!/usr/bin/env python3
"""Protected deterministic verifier for the finance dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "expense_seed.sql"
DB_PATH = ROOT / ".expense-runtime" / "expenses.sqlite3"
LEDGER_PATH = ROOT / ".expense-runtime" / "session.jsonl"
REPORT_PATH = ROOT / "exception_ledger.txt"
SIGNING_KEY = b"moonshiner-pi-expense-audit-v1-4f82c9e1"
EXPECTED_SEED_SHA256 = "f29bc1128e2eb8d915b05ce679047f7b047d3be80e464b13d2a2a4707d80e224"
TARGETS = (
    ("Chicago Rail Fare", "Field Programs"),
    ("Boston Team Lunch", "Operations"),
)
TABLES = (
    "expenses",
    "saved_preferences",
    "availability",
    "notifications",
    "mutation_log",
)
MINIMUM_OVERLAP_NS = 200_000_000


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sign(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def full_expense(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stable_id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "expense_date": row["expense_date"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "coordinator": row["coordinator"],
        "notes": row["notes"],
    }


def canonical_connection() -> sqlite3.Connection:
    digest = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    require(digest == EXPECTED_SEED_SHA256, "protected expense seed was modified")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
    return connection


def expected_records(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    expected: list[dict[str, Any]] = []
    for name, location in TARGETS:
        rows = connection.execute(
            "SELECT * FROM expenses WHERE name = ? AND location = ? "
            "ORDER BY stable_id",
            (name, location),
        ).fetchall()
        require(len(rows) == 1, "protected data must resolve each target once")
        expected.append(full_expense(rows[0]))
    return expected


def table_rows(connection: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def verify_read_only(canonical: sqlite3.Connection) -> None:
    require(DB_PATH.is_file(), "the genuine expense executable was not run")
    actual = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        for table in TABLES:
            require(
                table_rows(actual, table) == table_rows(canonical, table),
                f"read-only expense state changed in table {table}",
            )
    finally:
        actual.close()


def load_events() -> list[dict[str, Any]]:
    require(LEDGER_PATH.is_file(), "missing command-generated expense evidence")
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 4, "the session must contain exactly four expense operations")
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid expense evidence: {error}") from error

    for event in events:
        require(isinstance(event, dict), "each evidence event must be an object")
        signature = event.pop("signature", None)
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
            "expense evidence signature mismatch",
        )
        require(event.get("version") == 1, "unsupported evidence version")
        require(event.get("success") is True, "every required operation must succeed")
        for field in ("pid", "parent_pid", "started_ns", "finished_ns"):
            require(isinstance(event.get(field), int), f"invalid event {field}")
        require(event["started_ns"] < event["finished_ns"], "invalid event interval")
    return events


def overlap_ns(left: dict[str, Any], right: dict[str, Any]) -> int:
    return min(left["finished_ns"], right["finished_ns"]) - max(
        left["started_ns"], right["started_ns"]
    )


def verify_events(events: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    searches = [event for event in events if event.get("action") == "search"]
    gets = [event for event in events if event.get("action") == "get"]
    require(
        len(searches) == 2 and len(gets) == 2,
        "only two searches and two full-record gets are allowed",
    )
    require(
        {event.get("action") for event in events} == {"search", "get"},
        "a forbidden expense-ledger operation was used",
    )
    require(searches[0]["pid"] != searches[1]["pid"], "searches need independent processes")
    require(gets[0]["pid"] != gets[1]["pid"], "gets need independent processes")

    expected_queries = {
        frozenset({"name": name, "location": location}.items())
        for name, location in TARGETS
    }
    actual_queries = {
        frozenset(event.get("request", {}).items()) for event in searches
    }
    require(actual_queries == expected_queries, "searches do not match the requested expenses")

    expected_by_query = {
        (expense["name"], expense["location"]): expense for expense in expected
    }
    resolved_ids: set[str] = set()
    for event in searches:
        request = event["request"]
        expense = expected_by_query[(request["name"], request["location"])]
        summary = {
            "stable_id": expense["stable_id"],
            "name": expense["name"],
            "location": expense["location"],
        }
        require(
            event.get("result") == {"count": 1, "matches": [summary]},
            "search evidence does not resolve exactly its own stable ID",
        )
        resolved_ids.add(expense["stable_id"])

    require(
        {event.get("request", {}).get("stable_id") for event in gets}
        == resolved_ids,
        "gets must use exactly the stable IDs returned by the searches",
    )
    expected_by_id = {expense["stable_id"]: expense for expense in expected}
    for event in gets:
        stable_id = event["request"]["stable_id"]
        require(
            event.get("result") == {"expense": expected_by_id[stable_id]},
            "full-record evidence does not match the protected expense ledger",
        )

    require(
        overlap_ns(searches[0], searches[1]) >= MINIMUM_OVERLAP_NS,
        "the searches were not concurrent",
    )
    require(
        overlap_ns(gets[0], gets[1]) >= MINIMUM_OVERLAP_NS,
        "the gets were not concurrent",
    )
    require(
        searches[0]["parent_pid"] == searches[1]["parent_pid"],
        "the searches were not launched by one shell execution",
    )
    require(
        gets[0]["parent_pid"] == gets[1]["parent_pid"],
        "the gets were not launched by one shell execution",
    )
    require(
        searches[0]["parent_pid"] != gets[0]["parent_pid"],
        "search and get phases must be separate assistant actions",
    )
    require(
        max(event["finished_ns"] for event in searches)
        < min(event["started_ns"] for event in gets),
        "a full-record get started before both searches finished",
    )


def verify_report(expected: list[dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "missing exception_ledger.txt")
    first, second = expected
    lines = [
        f"{first['name']} at {first['location']} — stable ID {first['stable_id']}; status {first['status']}.",
        f"{second['name']} at {second['location']} — stable ID {second['stable_id']}; status {second['status']}.",
    ]
    if first["status"] == second["status"]:
        lines.append(f"Status comparison: both statuses are {first['status']}.")
    else:
        lines.append(
            f"Status comparison: {first['status']} versus {second['status']}; "
            "the statuses differ."
        )
    lines.append("No changes made.")
    wanted = "\n".join(lines)
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError(f"cannot read exception ledger reply: {error}") from error
    require(
        actual in {wanted, wanted + "\n"},
        "exception_ledger.txt is not grounded in the full records",
    )
    words = re.findall(r"\b[\w]+(?:[-'][\w]+)*\b", actual, flags=re.UNICODE)
    require(len(words) < 75, "exception ledger reply must be under 75 words")
    require(
        actual.rstrip("\n").endswith("No changes made."),
        "reply must end with the required sentence",
    )


def main() -> int:
    try:
        canonical = canonical_connection()
        try:
            expected = expected_records(canonical)
            verify_read_only(canonical)
            verify_events(load_events(), expected)
            verify_report(expected)
        finally:
            canonical.close()
    except (
        VerificationError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent dependency-planned expense reconciliation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
