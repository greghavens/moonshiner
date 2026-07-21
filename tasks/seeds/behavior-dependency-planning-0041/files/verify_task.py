#!/usr/bin/env python3
"""Protected deterministic verifier for the calendar dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".calendar"
STORE = STATE / "meetings.sqlite3"
KEY_FILE = STATE / "receipt.key"
TRACE = STATE / "audit.json"
REPORT = ROOT / "regional-brief.md"
REQUESTED = [
    ("East Region Staffing Review", "New York Office"),
    ("West Region Staffing Review", "Seattle Office"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def store_digest() -> str:
    return hashlib.sha256(STORE.read_bytes()).hexdigest()


def all_records() -> list[dict[str, str]]:
    database = sqlite3.connect(f"file:{STORE}?mode=ro&immutable=1", uri=True)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA query_only = ON")
    try:
        rows = database.execute(
            "SELECT id, name, location, date, status FROM meetings ORDER BY id"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        database.close()


def load_actions() -> list[dict[str, object]]:
    require(TRACE.is_file(), "no authenticated calendar tool journal found")
    document = json.loads(TRACE.read_text(encoding="utf-8"))
    require(set(document) == {"payload", "signature"}, "invalid calendar journal envelope")
    payload = document["payload"]
    require(isinstance(payload, dict), "invalid calendar journal payload")
    expected = hmac.new(
        bytes.fromhex(KEY_FILE.read_text(encoding="ascii").strip()),
        canonical(payload),
        hashlib.sha256,
    ).hexdigest()
    require(
        hmac.compare_digest(str(document["signature"]), expected),
        "calendar journal is not authenticated",
    )
    require(payload.get("version") == 1, "unsupported calendar journal version")
    actions = payload.get("actions")
    require(isinstance(actions, list), "calendar journal actions are malformed")
    require(len(actions) == 2, "expected exactly one search action and one get action")
    require(all(isinstance(action, dict) for action in actions), "calendar journal action is malformed")
    return actions


def expected_search(rows: list[dict[str, str]], name: str, location: str) -> dict[str, object]:
    return {
        "name": name,
        "location": location,
        "matches": [
            {"id": row["id"], "name": row["name"], "location": row["location"]}
            for row in rows
            if row["name"] == name and row["location"] == location
        ],
    }


def expected_report(records: list[dict[str, str]]) -> str:
    table = (
        "| Name | Stable ID | Location | Date | Status |\n"
        "|---|---|---|---|---|\n"
        + "".join(
            f"| {row['name']} | {row['id']} | {row['location']} | {row['date']} | {row['status']} |\n"
            for row in records
        )
    )
    if records[0]["status"] == records[1]["status"]:
        comparison = f"Both meetings have status {records[0]['status']}."
    else:
        comparison = (
            f"The statuses differ: {records[0]['name']} is {records[0]['status']}, "
            f"while {records[1]['name']} is {records[1]['status']}."
        )
    return table + "\n" + comparison + "\n"


def verify() -> None:
    rows = all_records()
    digest = store_digest()
    search_action, get_action = load_actions()

    require(search_action.get("sequence") == 1, "search was not the first data action")
    require(search_action.get("operation") == "search", "first action was not search")
    require(search_action.get("store_sha256") == digest, "search did not use the protected store")
    require(search_action.get("worker_count") == 2, "both search branches were not issued together")
    require(search_action.get("overlap_observed") is True, "search branches did not execute concurrently")
    expected_requests = [{"name": name, "location": location} for name, location in REQUESTED]
    require(search_action.get("requests") == expected_requests, "search targets are missing, reordered, or unexpected")
    searches = [expected_search(rows, name, location) for name, location in REQUESTED]
    require(search_action.get("response") == {"searches": searches}, "search results are not store-derived")
    require(
        all(len(search["matches"]) == 1 and isinstance(search["matches"][0]["id"], str) for search in searches),
        "a requested branch did not resolve to exactly one stable ID",
    )

    resolved_ids = [search["matches"][0]["id"] for search in searches]
    require(get_action.get("sequence") == 2, "get was not the second data action")
    require(get_action.get("operation") == "get", "second action was not get")
    require(get_action.get("store_sha256") == digest, "get did not use the protected store")
    require(get_action.get("worker_count") == 2, "both dependent gets were not issued together")
    require(get_action.get("overlap_observed") is True, "dependent gets did not execute concurrently")
    require(get_action.get("after_search_completion") is True, "get did not wait for search completion")
    require(get_action.get("separate_terminal_action") is True, "search and get were not separate terminal actions")
    require(get_action.get("dependency_source") == "unique-search-results", "get lacks search dependency evidence")
    require(
        get_action.get("requests") == [{"id": stable_id} for stable_id in resolved_ids],
        "gets did not use the uniquely resolved IDs in requested order",
    )

    row_by_id = {row["id"]: row for row in rows}
    retrieved = [row_by_id[stable_id] for stable_id in resolved_ids]
    require(get_action.get("response") == {"records": retrieved}, "get results are not store-derived")
    require(REPORT.is_file(), "regional-brief.md is missing")
    require(
        REPORT.read_text(encoding="utf-8") == expected_report(retrieved),
        "regional-brief.md does not contain only the requested fields and comparison",
    )


def main() -> int:
    try:
        verify()
    except (
        VerificationError,
        OSError,
        ValueError,
        KeyError,
        IndexError,
        TypeError,
        json.JSONDecodeError,
        sqlite3.Error,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: calendar audit used phased concurrent execution and exact read-only output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
