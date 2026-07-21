#!/usr/bin/env python3
"""Protected, deterministic acceptance gate for the permit audit."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "permit_queue.sqlite3"
EVENTS = ROOT / ".pytest_cache" / "permit-tool-events.jsonl"
REPORT = ROOT / "audit.md"

TARGETS = [
    {
        "name": "Historic Signage Review Application",
        "location": "Pueblo",
        "id": "pub-179",
        "date": "2026-10-13",
        "status": "additional-info-needed",
    },
    {
        "name": "Block Party Permit Application",
        "location": "Fort Collins",
        "id": "pub-579",
        "date": "unknown",
        "status": "approved",
    },
]
FORBIDDEN = {"list", "profile", "availability", "create", "update", "cancel", "notify"}


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fail(message: str) -> None:
    raise AssertionError(message)


def read_database() -> tuple[sqlite3.Connection, bytes]:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        "SELECT value FROM registry_metadata WHERE key = 'audit_secret'"
    ).fetchone()
    if row is None:
        fail("registry metadata is missing")
    return connection, row["value"].encode("utf-8")


def assert_registry_unchanged(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT id, name, location, date, status FROM applications ORDER BY id"
    ).fetchall()
    expected = [
        ("pub-179", "Historic Signage Review Application", "Pueblo", "2026-10-13", "additional-info-needed"),
        ("pub-579", "Block Party Permit Application", "Fort Collins", None, "approved"),
        ("pub-879", "Block Party Permit Application", "Fort Collins County", "2025-10-14", "expired"),
        ("pub-979", "Historic Signage Review Application", "Pueblo County", "2025-10-13", "expired"),
    ]
    observed = [tuple(row) for row in rows]
    if observed != expected:
        fail("application registry was modified")
    if connection.execute("SELECT COUNT(*) FROM mutation_log").fetchone()[0] != 0:
        fail("a mutating operation was attempted")
    if connection.execute("SELECT COUNT(*) FROM notification_log").fetchone()[0] != 0:
        fail("a notification was sent")


def read_events(secret: bytes) -> list[dict[str, object]]:
    if not EVENTS.is_file():
        fail("no genuine permitctl execution evidence was recorded")
    events = []
    for line_number, line in enumerate(EVENTS.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"invalid execution evidence on line {line_number}: {error}")
        signature = event.pop("signature", None)
        expected_signature = hmac.new(secret, canonical(event), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(signature), expected_signature):
            fail("execution evidence was not emitted by permitctl")
        if event.get("ended_ns", 0) <= event.get("started_ns", 0):
            fail("invalid tool execution interval")
        events.append(event)
    if any(event.get("operation") in FORBIDDEN for event in events):
        fail("a forbidden registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches and two eligible retrievals")
    return events


def result_digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def assert_tool_plan(events: list[dict[str, object]]) -> None:
    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("the tool plan must contain two searches followed by two retrievals")

    expected_searches = {
        (target["name"], target["location"]): target for target in TARGETS
    }
    seen_searches = set()
    for event in searches:
        arguments = event.get("arguments") or {}
        key = (arguments.get("name"), arguments.get("location"))
        target = expected_searches.get(key)
        if target is None or key in seen_searches:
            fail("searches did not use the two requested name-and-location pairs exactly once")
        seen_searches.add(key)
        summary = {
            "matches": [
                {
                    "id": target["id"],
                    "name": target["name"],
                    "location": target["location"],
                }
            ]
        }
        if event.get("result_digest") != result_digest(summary):
            fail("search execution did not return the expected unique stable ID")

    if len({event["parent_pid"] for event in searches}) != 1:
        fail("both searches must be launched in one shell-tool action")
    if not all(event.get("parallel_peer") is True for event in searches):
        fail("the two searches were not executed concurrently")

    expected_gets = {target["id"]: target for target in TARGETS}
    seen_gets = set()
    for event in gets:
        arguments = event.get("arguments") or {}
        stable_id = arguments.get("id")
        target = expected_gets.get(stable_id)
        if target is None or stable_id in seen_gets:
            fail("retrievals did not use each uniquely returned stable ID exactly once")
        seen_gets.add(stable_id)
        record = {
            "id": target["id"],
            "name": target["name"],
            "location": target["location"],
            "status": target["status"],
        }
        if target["date"] != "unknown":
            record["date"] = target["date"]
        if event.get("result_digest") != result_digest({"record": record}):
            fail("retrieval evidence does not match the full record returned by permitctl")

    if min(event["started_ns"] for event in gets) < max(
        event["ended_ns"] for event in searches
    ):
        fail("a retrieval started before both searches had completed")
    if len({event["parent_pid"] for event in gets}) != 1:
        fail("both retrievals must be launched in one shell-tool action")
    if searches[0]["parent_pid"] == gets[0]["parent_pid"]:
        fail("searches and retrievals must be separate shell-tool actions")
    if not all(event.get("parallel_peer") is True for event in gets):
        fail("the two retrievals were not executed concurrently")


def parse_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def assert_report() -> None:
    if not REPORT.is_file():
        fail("audit.md is missing")
    lines = [line.rstrip() for line in REPORT.read_text(encoding="utf-8").splitlines()]
    content_lines = [line.strip() for line in lines if line.strip()]
    if len(content_lines) < 5 or not all(
        line.startswith("|") for line in content_lines[:4]
    ) or any(line.startswith("|") for line in content_lines[4:]):
        fail("audit.md must contain only the requested table followed by one sentence")
    table_lines = content_lines[:4]
    if parse_row(table_lines[0]) != ["Name", "ID", "Date", "Status"]:
        fail("audit table columns or order are incorrect")
    delimiter = parse_row(table_lines[1])
    if len(delimiter) != 4 or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in delimiter):
        fail("audit table delimiter is invalid")
    observed_rows = [parse_row(line) for line in table_lines[2:]]
    expected_rows = [
        [target["name"], target["id"], target["date"], target["status"]]
        for target in TARGETS
    ]
    if observed_rows != expected_rows:
        fail("audit rows are not grounded in the retrieved records")

    sentence = " ".join(content_lines[4:])
    if not re.search(r"[.!?]$", sentence) or len(
        re.findall(r"[.!?](?=\s|$)", sentence)
    ) != 1:
        fail("the comparison must be exactly one sentence after the table")
    lowered = sentence.casefold()
    if lowered.count("additional-info-needed") != 1 or lowered.count("approved") != 1:
        fail("add one concise sentence comparing both retrieved status values")
    comparison_cue = re.search(
        r"\b(?:and|but|compare(?:d)?|comparison|contrast(?:s|ed|ing)?|"
        r"differ(?:s|ed|ent)?|rather|respectively|unlike|versus|whereas|while|yet)\b",
        lowered,
    )
    if comparison_cue is None and ";" not in sentence:
        fail("the final sentence must explicitly compare the status values")


def main() -> int:
    try:
        connection, secret = read_database()
        assert_registry_unchanged(connection)
        events = read_events(secret)
        assert_tool_plan(events)
        assert_report()
        connection.close()
    except (AssertionError, OSError, sqlite3.Error) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: grounded parallel permit audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
