#!/usr/bin/env python3
"""Protected acceptance verifier for the executable-tool audit workflow."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".branch_catalog"
REPORT = ROOT / "handoff-audit.md"
AUDIT_LOG = RUNTIME / "audit.jsonl"
DATABASE = RUNTIME / "catalog.sqlite"

EXPECTED_SEARCHES = {
    ("Orchard Weather Journal", "North Branch"): "lib-133",
    ("A Field Guide to Civic Murals", "Downtown Branch"): "lib-533",
}
FORBIDDEN = {
    "list",
    "preferences",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def database_rows(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        "records": connection.execute(
            "SELECT stable_id, name, location, status, record_date FROM records ORDER BY stable_id"
        ).fetchall(),
        "saved_preferences": connection.execute(
            "SELECT preference_key, preference_value FROM saved_preferences ORDER BY preference_key"
        ).fetchall(),
        "notifications": connection.execute(
            "SELECT stable_id, message FROM notifications ORDER BY notification_id"
        ).fetchall(),
        "mutation_log": connection.execute(
            "SELECT operation, stable_id FROM mutation_log ORDER BY mutation_id"
        ).fetchall(),
    }


def assert_database_unchanged() -> None:
    if not DATABASE.is_file():
        fail("catalog environment was not initialized")
    actual = sqlite3.connect(DATABASE)
    expected = sqlite3.connect(":memory:")
    try:
        expected.executescript((ROOT / "data" / "catalog.sql").read_text())
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("catalog database integrity check failed")
        if database_rows(actual) != database_rows(expected):
            fail("read-only audit changed catalog state")
    finally:
        actual.close()
        expected.close()


def read_audit() -> list[dict]:
    if not AUDIT_LOG.is_file():
        fail("no executable-tool audit log was produced")
    entries = []
    for line_number, line in enumerate(AUDIT_LOG.read_text().splitlines(), 1):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            fail(f"malformed executable-tool audit entry on line {line_number}")
        if not isinstance(entry, dict):
            fail(f"invalid executable-tool audit entry on line {line_number}")
        entries.append(entry)
    return entries


def assert_overlap(entries: list[dict], label: str) -> int:
    if len(entries) != 2:
        fail(f"expected exactly two {label} operations")
    if len({entry.get("pid") for entry in entries}) != 2:
        fail(f"{label} operations were not separate executable processes")
    parents = {entry.get("ppid") for entry in entries}
    if len(parents) != 1 or None in parents:
        fail(f"the two {label} operations were not issued together in one action")
    try:
        overlap = max(entry["started_ns"] for entry in entries) < min(
            entry["finished_ns"] for entry in entries
        )
    except (KeyError, TypeError):
        fail(f"{label} timing evidence is invalid")
    if not overlap:
        fail(f"the two {label} operations did not execute concurrently")
    return next(iter(parents))


def assert_workflow(entries: list[dict]) -> tuple[str, str]:
    if len(entries) != 4:
        fail("expected exactly four catalog operations: two searches and two retrieves")
    if any(entry.get("operation") in FORBIDDEN for entry in entries):
        fail("a forbidden catalog operation was used")
    if any(entry.get("outcome") != "ok" for entry in entries):
        fail("every catalog operation must complete successfully")

    searches = [entry for entry in entries if entry.get("operation") == "search"]
    gets = [entry for entry in entries if entry.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("workflow must contain only two searches followed by two retrieves")
    search_action = assert_overlap(searches, "search")
    get_action = assert_overlap(gets, "retrieve")
    if search_action == get_action:
        fail("searches and retrieves were not issued in successive actions")

    try:
        if min(entry["started_ns"] for entry in gets) <= max(
            entry["finished_ns"] for entry in searches
        ):
            fail("a retrieve began before both search results had returned")
    except (KeyError, TypeError):
        fail("stage dependency timing evidence is invalid")

    returned_ids: dict[tuple[str, str], str] = {}
    for entry in searches:
        arguments = entry.get("arguments")
        if not isinstance(arguments, dict):
            fail("search arguments were not recorded by the executable")
        pair = (arguments.get("name"), arguments.get("location"))
        if pair not in EXPECTED_SEARCHES:
            fail("search did not use one requested exact name-and-location pair")
        result = entry.get("result")
        matches = result.get("matches") if isinstance(result, dict) else None
        if not isinstance(matches, list) or len(matches) != 1:
            fail("a retrieve was attempted without a uniquely resolved search")
        stable_id = matches[0].get("stable_id") if isinstance(matches[0], dict) else None
        if stable_id != EXPECTED_SEARCHES[pair]:
            fail("search returned unexpected stable-ID evidence")
        returned_ids[pair] = stable_id
    if set(returned_ids) != set(EXPECTED_SEARCHES):
        fail("both requested search pairs were not completed")

    get_by_id = {}
    for entry in gets:
        arguments = entry.get("arguments")
        stable_id = arguments.get("stable_id") if isinstance(arguments, dict) else None
        if stable_id not in returned_ids.values() or stable_id in get_by_id:
            fail("retrieve ID was duplicated, guessed, or not returned by its search")
        result = entry.get("result")
        record = result.get("record") if isinstance(result, dict) else None
        if not isinstance(record, dict) or record.get("stable_id") != stable_id:
            fail("retrieve result is missing its requested stable record")
        get_by_id[stable_id] = record

    first = get_by_id[EXPECTED_SEARCHES[("Orchard Weather Journal", "North Branch")]]
    second = get_by_id[
        EXPECTED_SEARCHES[("A Field Guide to Civic Murals", "Downtown Branch")]
    ]
    return first["status"], second["status"]


def assert_report(first_status: str, second_status: str) -> None:
    if not REPORT.is_file():
        fail("handoff-audit.md is missing")
    raw = REPORT.read_text()
    lines = raw.splitlines()
    if len(lines) != 2 or any(not line.startswith("- ") for line in lines):
        fail("handoff-audit.md must contain exactly two Markdown bullets and no other text")

    comparison = (
        "the statuses are the same" if first_status == second_status else "the statuses differ"
    )
    expected = [
        f"- Orchard Weather Journal — North Branch: {first_status}",
        f"- A Field Guide to Civic Murals — Downtown Branch: {second_status}; {comparison}",
    ]
    if lines != expected:
        fail("handoff-audit.md does not match the required retrieved-status lines")


def main() -> int:
    assert_database_unchanged()
    first_status, second_status = assert_workflow(read_audit())
    assert_report(first_status, second_status)
    print("PASS: genuine parallel catalog audit and exact two-bullet deliverable verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
