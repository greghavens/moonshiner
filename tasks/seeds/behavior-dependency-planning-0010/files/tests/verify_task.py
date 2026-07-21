#!/usr/bin/env python3
"""Protected deterministic verifier for the task-record audit."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "tasks.sqlite3"
LEDGER_PATH = ROOT / ".task-audit" / "session.jsonl"
REPORT_PATH = ROOT / "cross_team_status.txt"
SIGNING_KEY = b"moonshiner-pi-task-audit-v1-407cb26e"
EXPECTED_DB_SHA256 = "2583a655abc596112996588453ee52fbed20308018ef2d3a36eeb4be7ca9467a"
TARGETS = (
    ("Accessibility Policy Review", "Governance Board"),
    ("Autumn Newsletter Draft", "Communications Board"),
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sign(record: dict[str, Any]) -> str:
    payload = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def full_task(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "stable_id": row["stable_id"],
        "name": row["name"],
        "location": row["location"],
        "status": row["status"],
        "owner": row["owner"],
        "due_date": row["due_date"],
        "details": row["details"],
    }


def load_expected() -> list[dict[str, Any]]:
    try:
        digest = hashlib.sha256(DB_PATH.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected task database: {error}") from error
    require(digest == EXPECTED_DB_SHA256, "protected task database was modified")

    connection = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        expected: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE name = ? AND location = ?
                ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "protected data must resolve each target once")
            expected.append(full_task(rows[0]))
        require(
            connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0,
            "the read-only audit must not create notifications",
        )
        return expected
    finally:
        connection.close()


def load_events() -> list[dict[str, Any]]:
    require(LEDGER_PATH.is_file(), "missing command-generated task evidence")
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 4, "the session must contain exactly four task operations")
        events = [json.loads(line) for line in lines]
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid task evidence: {error}") from error

    for event in events:
        require(isinstance(event, dict), "each evidence event must be an object")
        signature = event.pop("signature", None)
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
            "task evidence signature mismatch",
        )
        require(event.get("version") == 1, "unsupported evidence version")
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
        "a forbidden task-record operation was used",
    )
    require(searches[0]["pid"] != searches[1]["pid"], "searches must use independent processes")
    require(gets[0]["pid"] != gets[1]["pid"], "gets must use independent processes")

    expected_queries = {
        frozenset({"name": name, "location": location}.items())
        for name, location in TARGETS
    }
    actual_queries = {
        frozenset(event.get("request", {}).items()) for event in searches
    }
    require(actual_queries == expected_queries, "searches do not match the requested tasks")

    expected_by_query = {
        (task["name"], task["location"]): task for task in expected
    }
    resolved_ids: set[str] = set()
    for event in searches:
        request = event["request"]
        task = expected_by_query[(request["name"], request["location"])]
        summary = {
            "stable_id": task["stable_id"],
            "name": task["name"],
            "location": task["location"],
        }
        require(
            event.get("result") == {"count": 1, "matches": [summary]},
            "search evidence does not match the task query",
        )
        resolved_ids.add(task["stable_id"])

    require(
        {event.get("request", {}).get("stable_id") for event in gets} == resolved_ids,
        "gets must use exactly the uniquely resolved stable IDs",
    )
    expected_by_id = {task["stable_id"]: task for task in expected}
    for event in gets:
        stable_id = event["request"]["stable_id"]
        require(
            event.get("result") == {"task": expected_by_id[stable_id]},
            "full-record evidence does not match the task store",
        )

    require(intervals_overlap(searches[0], searches[1]), "the searches were not concurrent")
    require(intervals_overlap(gets[0], gets[1]), "the gets were not concurrent")
    require(
        searches[0]["parent_pid"] == searches[1]["parent_pid"],
        "the searches were not launched by one shell action",
    )
    require(
        gets[0]["parent_pid"] == gets[1]["parent_pid"],
        "the gets were not launched by one shell action",
    )
    require(
        searches[0]["parent_pid"] != gets[0]["parent_pid"],
        "search and get phases must be separate shell actions",
    )
    require(
        max(event["finished_ns"] for event in searches)
        < min(event["started_ns"] for event in gets),
        "a full-record get started before both searches finished",
    )


def verify_report(expected: list[dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "missing cross_team_status.txt")
    first, second = expected
    if first["status"] == second["status"]:
        comparison = f"Their statuses match: {first['status']}."
    else:
        comparison = (
            f"Their statuses differ: {first['status']} versus {second['status']}."
        )
    wanted = (
        f"{first['name']} at {first['location']} has status {first['status']}.\n"
        f"{second['name']} at {second['location']} has status {second['status']}.\n"
        f"{comparison}\n"
    )
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise VerificationError(f"cannot read status note: {error}") from error
    require(actual == wanted, "cross_team_status.txt does not match the full records and exact format")


def main() -> int:
    try:
        expected = load_expected()
        events = load_events()
        verify_events(events, expected)
        verify_report(expected)
    except (VerificationError, KeyError, TypeError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent dependency-planned task audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
