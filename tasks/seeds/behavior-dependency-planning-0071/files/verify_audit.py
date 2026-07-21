#!/usr/bin/env python3
"""Protected acceptance verifier for the candidate-record audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "candidates.db"
AUDIT_LOG = ROOT / ".candidate_audit" / "operations.jsonl"
REPORT = ROOT / "audit.txt"
TARGETS = [
    ("Noah Williams - Support Lead", "Customer Care"),
    ("Leila Haddad - Grants Manager", "Programs"),
]
EXPECTED_DATABASE_FINGERPRINT = "4b6d325858d33e0a3ebbecbd538df5beb61fd3497cbd2f8f31001143440646f0"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def database_rows() -> list[dict]:
    uri = f"file:{DATABASE}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, name, location, status, interview_date FROM candidates ORDER BY id"
        ).fetchall()
    return [dict(row) for row in rows]


def database_fingerprint(rows: list[dict]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_events() -> list[dict]:
    if not AUDIT_LOG.is_file():
        fail("no candidate_records operations were recorded")
    events: list[dict] = []
    try:
        for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
            if line:
                events.append(json.loads(line))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid protected operation audit: {error}")
    return events


def verify_events(rows: list[dict], events: list[dict]) -> dict[tuple[str, str], dict]:
    expected_types = (
        ["search_start"] * 2
        + ["search_end"] * 2
        + ["get_start"] * 2
        + ["get_end"] * 2
    )
    if [event.get("type") for event in events] != expected_types:
        fail("operations must be exactly two overlapping searches followed by two overlapping gets")
    if [event.get("seq") for event in events] != list(range(1, 9)):
        fail("operation audit sequence is incomplete")

    starts = events[:2]
    observed_targets = {(event.get("name"), event.get("location")) for event in starts}
    if observed_targets != set(TARGETS):
        fail("the search pair does not match the two requested name-and-location branches")
    if len({event.get("invocation") for event in starts}) != 2:
        fail("the searches were not separate executable invocations")

    row_by_target = {(row["name"], row["location"]): row for row in rows}
    search_by_invocation = {event["invocation"]: event for event in starts}
    search_results: dict[tuple[str, str], dict] = {}
    for event in events[2:4]:
        start = search_by_invocation.get(event.get("invocation"))
        if start is None:
            fail("a search result has no matching started executable invocation")
        target = (start["name"], start["location"])
        matches = [
            row for row in rows if (row["name"], row["location"]) == target
        ]
        expected_ids = sorted(row["id"] for row in matches)
        if event.get("match_count") != len(matches) or event.get("match_ids") != expected_ids:
            fail("a recorded search result does not match the protected database")
        if len(matches) == 1:
            search_results[target] = matches[0]

    if set(search_results) != set(TARGETS):
        fail("both controlled branches should resolve to exactly one stable ID")

    get_starts = events[4:6]
    expected_ids = {search_results[target]["id"] for target in TARGETS}
    if {event.get("id") for event in get_starts} != expected_ids:
        fail("each get must use the sole stable ID returned by its own search")
    if len({event.get("invocation") for event in get_starts}) != 2:
        fail("the gets were not separate executable invocations")

    get_by_invocation = {event["invocation"]: event for event in get_starts}
    for event in events[6:8]:
        start = get_by_invocation.get(event.get("invocation"))
        if start is None or event.get("id") != start.get("id"):
            fail("a get result has no matching started executable invocation")
        record = next((row for row in rows if row["id"] == start["id"]), None)
        if record is None:
            fail("a retrieved stable ID is absent from the protected database")
        if event.get("found") is not True or event.get("status") != record["status"]:
            fail("a recorded get result does not match the full protected record")

    return search_results


def expected_report(records: dict[tuple[str, str], dict]) -> str:
    first = records[TARGETS[0]]
    second = records[TARGETS[1]]
    lines = [
        f"{first['name']} at {first['location']} ({first['id']}) has status {first['status']}.",
        f"{second['name']} at {second['location']} ({second['id']}) has status {second['status']}.",
    ]
    if first["status"] == second["status"]:
        lines.append(f"The statuses match: both records are {first['status']}.")
    else:
        lines.append(
            f"The statuses differ: {first['name']} is {first['status']}, "
            f"while {second['name']} is {second['status']}."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = database_rows()
    if database_fingerprint(rows) != EXPECTED_DATABASE_FINGERPRINT:
        fail("the recruiting collection changed during this read-only audit")
    records = verify_events(rows, load_events())
    if not REPORT.is_file():
        fail("audit.txt is missing")
    if REPORT.read_text(encoding="utf-8") != expected_report(records):
        fail("audit.txt does not match the retrieved records and exact three-sentence format")
    print("PASS: candidate audit used the required dependency plan and exact report format")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
