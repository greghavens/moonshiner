#!/usr/bin/env python3
"""Protected deterministic verifier for the messaging dependency-plan audit."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "messages.sqlite3"
BASELINE_HASH = ROOT / "tests" / "baseline.sha256"
EVIDENCE = ROOT / ".audit-runtime"
REPORT = ROOT / "audit_report.txt"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_events() -> list[dict]:
    if not EVIDENCE.is_dir():
        fail("no executable messaging operations were recorded")
    events = []
    for path in sorted(EVIDENCE.glob("*.json")):
        try:
            events.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError) as error:
            fail(f"invalid runtime evidence: {error}")
    return events


def overlap(first: dict, second: dict) -> bool:
    return (
        first["started_ns"] < second["finished_ns"]
        and second["started_ns"] < first["finished_ns"]
    )


def database_rows(query: str, values: tuple = ()) -> list[dict]:
    uri = f"file:{DATABASE}?mode=ro"
    with sqlite3.connect(uri, uri=True) as database:
        database.row_factory = sqlite3.Row
        return [dict(row) for row in database.execute(query, values).fetchall()]


def main() -> int:
    if not REPORT.is_file():
        fail("audit_report.txt is missing")

    observed_hash = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    expected_hash = BASELINE_HASH.read_text().strip()
    if observed_hash != expected_hash:
        fail("the backing messaging state changed")

    events = load_events()
    if len(events) != 4:
        fail("the audit must contain exactly two searches followed by two gets")
    if any(event.get("version") != 1 for event in events):
        fail("runtime evidence version is invalid")

    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    if len(searches) != 2 or len(gets) != 2:
        fail("only two searches and two gets are permitted")

    targets = database_rows(
        "SELECT position, name, location FROM audit_targets ORDER BY position"
    )
    expected_pairs = {(target["name"], target["location"]) for target in targets}
    observed_pairs = {
        (event.get("parameters", {}).get("name"),
         event.get("parameters", {}).get("location"))
        for event in searches
    }
    if observed_pairs != expected_pairs:
        fail("the searches did not use both requested name-and-location pairs")

    if not overlap(searches[0], searches[1]):
        fail("the two searches were not executed concurrently")
    if min(event["started_ns"] for event in gets) < max(
        event["finished_ns"] for event in searches
    ):
        fail("a get began before both searches returned")
    if not overlap(gets[0], gets[1]):
        fail("the two gets were not executed concurrently")

    unique_ids: dict[tuple[str, str], str] = {}
    for event in searches:
        pair = (
            event["parameters"].get("name"),
            event["parameters"].get("location"),
        )
        matches = event.get("result", {}).get("matches")
        expected_matches = database_rows(
            "SELECT stable_id AS id, name, location FROM messages "
            "WHERE name = ? AND location = ? ORDER BY stable_id",
            pair,
        )
        if matches != expected_matches or len(matches) != 1:
            fail("a search result was not a genuine unique database result")
        unique_ids[pair] = matches[0]["id"]

    get_by_id = {
        event.get("parameters", {}).get("stable_id"): event for event in gets
    }
    if set(get_by_id) != set(unique_ids.values()):
        fail("each get must use the sole stable ID returned by its own search")

    records = []
    for target in targets:
        pair = (target["name"], target["location"])
        stable_id = unique_ids[pair]
        expected = database_rows(
            "SELECT stable_id AS id, name, location, status, send_date AS date "
            "FROM messages WHERE stable_id = ?",
            (stable_id,),
        )
        if len(expected) != 1:
            fail("a resolved stable ID no longer identifies one record")
        observed = get_by_id[stable_id].get("result", {}).get("record")
        if observed != expected[0]:
            fail("a get result does not match the backing record")
        records.append(expected[0])

    first, second = records
    if first["status"] == second["status"]:
        comparison = f"Both messages have status {first['status']}."
    else:
        comparison = (
            f"{first['name']} is {first['status']} while "
            f"{second['name']} is {second['status']}."
        )
    expected_report = "\n".join([
        f"{first['name']} at {first['location']} is {first['status']}.",
        f"{second['name']} at {second['location']} is {second['status']}.",
        comparison,
    ]) + "\n"
    if REPORT.read_text() != expected_report:
        fail("audit_report.txt does not have the required three exact sentences")

    print("PASS: audit report, read-only state, dependencies, and concurrency verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
