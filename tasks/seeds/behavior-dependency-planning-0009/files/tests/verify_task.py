#!/usr/bin/env python3
"""Protected deterministic verifier for the account-audit behavior task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "accounts.sqlite3"
LEDGER_PATH = ROOT / ".crm" / "session.jsonl"
REPORT_PATH = ROOT / "pipeline_review.md"
SIGNING_KEY = b"moonshiner-pi-account-audit-v1-6f8c1da2"
TARGETS = [
    ("Arbor Foods Cooperative", "West Region"),
    ("Bright Dental Group", "Central Region"),
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sign(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def full_account(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stable_id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "owner": row["owner"],
        "segment": row["segment"],
        "last_reviewed": row["last_reviewed"],
    }


def load_expected() -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        expected: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = connection.execute(
                """
                SELECT * FROM accounts
                WHERE name = ? AND location = ?
                ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "protected CRM fixture must resolve each target once")
            expected.append(full_account(rows[0]))
        require(
            connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0,
            "read-only audit must not create notifications",
        )
        return expected
    finally:
        connection.close()


def load_events() -> list[dict[str, Any]]:
    require(LEDGER_PATH.is_file(), "missing command-generated .crm/session.jsonl")
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 4, "the session must contain exactly four CRM operations")
        events = [json.loads(line) for line in lines]
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid command ledger: {error}") from error

    for event in events:
        require(isinstance(event, dict), "each ledger event must be an object")
        signature = event.pop("signature", None)
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
            "command ledger signature mismatch",
        )
        require(event.get("version") == 1, "unsupported ledger version")
        for field in ("pid", "parent_pid", "started_ns", "finished_ns"):
            require(isinstance(event.get(field), int), f"invalid event {field}")
        require(event["started_ns"] < event["finished_ns"], "invalid event interval")
    return events


def intervals_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return max(left["started_ns"], right["started_ns"]) < min(
        left["finished_ns"], right["finished_ns"]
    )


def verify_events(events: list[dict[str, Any]], expected: list[dict[str, Any]]) -> None:
    searches = [event for event in events if event.get("action") == "search"]
    gets = [event for event in events if event.get("action") == "get"]
    require(len(searches) == 2 and len(gets) == 2, "only two searches and two gets are allowed")
    require(
        {event.get("action") for event in events} == {"search", "get"},
        "a forbidden CRM operation was used",
    )
    require(
        searches[0]["pid"] != searches[1]["pid"],
        "the searches must run in independent concurrent processes",
    )
    require(
        gets[0]["pid"] != gets[1]["pid"],
        "the gets must run in independent concurrent processes",
    )

    expected_queries = {frozenset({"name": name, "location": location}.items()) for name, location in TARGETS}
    actual_queries = {
        frozenset(event.get("request", {}).items()) for event in searches
    }
    require(actual_queries == expected_queries, "search requests do not match the two requested records")

    expected_by_query = {
        (account["name"], account["location"]): account for account in expected
    }
    resolved_ids: set[str] = set()
    for event in searches:
        request = event["request"]
        account = expected_by_query[(request["name"], request["location"])]
        expected_summary = {
            "stable_id": account["stable_id"],
            "name": account["name"],
            "location": account["location"],
        }
        require(
            event.get("result") == {"count": 1, "matches": [expected_summary]},
            "search evidence does not match the CRM query",
        )
        resolved_ids.add(account["stable_id"])

    require(
        {event.get("request", {}).get("stable_id") for event in gets} == resolved_ids,
        "gets must use exactly the uniquely resolved stable IDs",
    )
    expected_by_id = {account["stable_id"]: account for account in expected}
    for event in gets:
        stable_id = event["request"]["stable_id"]
        require(
            event.get("result") == {"account": expected_by_id[stable_id]},
            "full-record evidence does not match the CRM record",
        )

    require(intervals_overlap(searches[0], searches[1]), "the two searches were not concurrent")
    require(intervals_overlap(gets[0], gets[1]), "the two gets were not concurrent")
    require(
        searches[0]["parent_pid"] == searches[1]["parent_pid"],
        "the searches were not launched by one shell-tool action",
    )
    require(
        gets[0]["parent_pid"] == gets[1]["parent_pid"],
        "the gets were not launched by one shell-tool action",
    )
    require(
        max(event["finished_ns"] for event in searches)
        < min(event["started_ns"] for event in gets),
        "full-record gets started before both searches finished",
    )
    require(
        min(event["started_ns"] for event in gets)
        - max(event["finished_ns"] for event in searches)
        >= 250_000_000,
        "searches and gets were collapsed into one shell-tool action",
    )


def verify_report(expected: list[dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "missing pipeline_review.md")
    first, second = expected
    wanted = (
        "## First record\n"
        f"Name: {first['name']}\n"
        f"Location: {first['location']}\n"
        f"Status: {first['status']}\n\n"
        "## Second record\n"
        f"Name: {second['name']}\n"
        f"Location: {second['location']}\n"
        f"Status: {second['status']}\n\n"
        f"{first['name']} is {first['status']}, while "
        f"{second['name']} is {second['status']}.\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise VerificationError(f"cannot read report: {error}") from error
    require(actual == wanted, "pipeline_review.md does not match the retrieved full records and exact format")


def main() -> int:
    try:
        expected = load_expected()
        events = load_events()
        verify_events(events, expected)
        verify_report(expected)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent dependency-planned account audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
