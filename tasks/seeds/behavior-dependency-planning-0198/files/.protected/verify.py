#!/usr/bin/env python3
"""Protected deterministic acceptance checks for the subscription review."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "subscriptions.sqlite3"
RUNTIME_DB = ROOT / "var" / "runtime-subscriptions.sqlite3"
AUDIT_PATH = ROOT / "audit.log"
REPORT_PATH = ROOT / "subscription-review.md"
REQUESTED = (
    ("Family fiber subscription", "Family Account"),
    ("Studio tablet data plan", "Studio Account"),
)
FORBIDDEN = {"update", "cancel", "notify"}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_records(path: Path) -> list[dict[str, Any]]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in db.execute(
                """SELECT stable_id, name, account, status, service_date,
                          plan_type, notes
                   FROM subscriptions ORDER BY stable_id"""
            ).fetchall()
        ]
    finally:
        db.close()


def expected_records() -> list[dict[str, Any]]:
    all_records = read_records(DB_PATH)
    records: list[dict[str, Any]] = []
    for name, account in REQUESTED:
        matches = [
            row
            for row in all_records
            if str(row["name"]).casefold() == name.casefold()
            and str(row["account"]).casefold() == account.casefold()
        ]
        require(len(matches) == 1, f"protected fixture must resolve once: {name!r}")
        records.append(matches[0])
    return records


def verify_state() -> None:
    if not RUNTIME_DB.exists():
        return
    require(
        read_records(RUNTIME_DB) == read_records(DB_PATH),
        "subscription state changed during the read-only review",
    )
    db = sqlite3.connect(f"file:{RUNTIME_DB}?mode=ro", uri=True)
    try:
        notifications = db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    finally:
        db.close()
    require(notifications == 0, "a subscription notification was created")


def load_events() -> list[dict[str, Any]]:
    require(AUDIT_PATH.is_file(), "no subscription-client execution audit exists")
    events: list[dict[str, Any]] = []
    for number, line in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise VerificationError(f"audit line {number} is invalid JSON: {error}") from error
        require(isinstance(event, dict), f"audit line {number} is not an object")
        events.append(event)
    return events


def interval(event: dict[str, Any]) -> tuple[int, int]:
    start, end = event.get("started_ns"), event.get("ended_ns")
    require(
        isinstance(start, int) and isinstance(end, int) and start < end,
        "audit event has an invalid execution interval",
    )
    return start, end


def require_overlap(events: list[dict[str, Any]], label: str) -> None:
    require(len(events) == 2, f"expected exactly two {label} executions")
    batches = {event.get("concurrency_batch") for event in events}
    require(
        len(batches) == 1 and None not in batches,
        f"the two {label} executions did not share a concurrency rendezvous",
    )
    first, second = interval(events[0]), interval(events[1])
    require(
        max(first[0], second[0]) < min(first[1], second[1]),
        f"the two {label} executions did not overlap",
    )


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()


def verify_events(events: list[dict[str, Any]], records: list[dict[str, Any]]) -> None:
    require(events, "the executable subscription client was not used")
    operations = [event.get("operation") for event in events]
    forbidden = sorted({str(op) for op in operations if op in FORBIDDEN})
    require(not forbidden, "forbidden operations used: " + ", ".join(forbidden))
    require(len(events) == 4, "the audit must contain exactly two searches and two gets")
    require(all(event.get("ok") is True for event in events), "every required call must succeed")

    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    require(
        len(searches) == 2 and len(gets) == 2,
        "the audit must contain two searches followed by two gets",
    )

    expected_by_pair = {
        (record["name"], record["account"]): record["stable_id"] for record in records
    }
    seen_pairs: set[tuple[str, str]] = set()
    for event in searches:
        evidence = event.get("evidence")
        require(isinstance(evidence, dict), "search evidence is missing")
        pair = (evidence.get("name"), evidence.get("account"))
        require(pair in expected_by_pair, "search used the wrong name or account")
        require(pair not in seen_pairs, "a requested search was repeated")
        seen_pairs.add(pair)
        require(evidence.get("match_count") == 1, "each retrieved branch must resolve uniquely")
        require(
            evidence.get("stable_ids") == [expected_by_pair[pair]],
            "search did not resolve the protected stable ID",
        )
    require(seen_pairs == set(expected_by_pair), "both requested searches are required")

    expected_ids = {record["stable_id"] for record in records}
    seen_ids: set[str] = set()
    for event in gets:
        evidence = event.get("evidence")
        require(isinstance(evidence, dict), "get evidence is missing")
        stable_id = evidence.get("stable_id")
        require(stable_id in expected_ids, "get used an ID not resolved by a requested search")
        require(stable_id not in seen_ids, "a requested get was repeated")
        seen_ids.add(str(stable_id))
        record = next(item for item in records if item["stable_id"] == stable_id)
        require(
            evidence.get("found") is True
            and evidence.get("record_digest") == record_digest(record),
            "get did not return the protected complete record",
        )
    require(seen_ids == expected_ids, "both uniquely resolved records must be retrieved")

    require_overlap(searches, "search")
    require_overlap(gets, "get")
    latest_search_end = max(interval(event)[1] for event in searches)
    earliest_get_start = min(interval(event)[0] for event in gets)
    require(
        earliest_get_start > latest_search_end,
        "retrieval began before both search results completed",
    )


def rendered(value: object) -> str:
    return "unknown" if value is None or value == "" else str(value)


def row_cells(line: str) -> list[str]:
    content = line.strip()
    if content.startswith("|"):
        content = content[1:]
    if content.endswith("|"):
        content = content[:-1]
    return [cell.strip() for cell in content.split("|")]


def parse_row(line: str, number: int) -> list[str]:
    cells = row_cells(line)
    require(len(cells) == 5, f"table row {number} must have five cells")
    return cells


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "subscription-review.md was not created")
    lines = REPORT_PATH.read_text(encoding="utf-8").splitlines()
    headers = [
        index
        for index, line in enumerate(lines)
        if row_cells(line) == ["Name", "Account", "ID", "Status", "Date"]
    ]
    separators = [
        index
        for index, line in enumerate(lines)
        if "|" in line
        and len(row_cells(line)) >= 2
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in row_cells(line))
    ]
    require(
        len(headers) == 1 and separators == [headers[0] + 1],
        "subscription-review.md must contain exactly one table with the required header",
    )
    table_start = headers[0]
    require(len(lines) > table_start + 3, "the table must contain two record rows")
    require(
        parse_row(lines[table_start], table_start + 1)
        == ["Name", "Account", "ID", "Status", "Date"],
        "table columns are missing or out of order",
    )
    separator = parse_row(lines[table_start + 1], table_start + 2)
    require(
        all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator),
        "table separator is malformed",
    )

    for index, record in enumerate(records):
        line_number = table_start + index + 3
        actual = parse_row(lines[table_start + index + 2], line_number)
        expected = [
            str(record["name"]),
            rendered(record["account"]),
            rendered(record["stable_id"]),
            rendered(record["status"]),
            rendered(record["service_date"]),
        ]
        require(actual == expected, f"record row {index + 1} is not grounded in the complete record")

    comparison_lines = [line for line in lines[table_start + 4 :] if line.strip()]
    require(len(comparison_lines) == 1, "add one comparison sentence after the table")
    comparison = comparison_lines[0].casefold()
    statuses = [rendered(record["status"]).casefold() for record in records]
    dates = [rendered(record["service_date"]).casefold() for record in records]
    require(all(value in comparison for value in statuses), "comparison must name both status values")
    require(all(value in comparison for value in dates), "comparison must name both date values")

    if "unknown" in statuses:
        require("unavailable" in comparison, "unknown status makes status comparison unavailable")
    elif statuses[0] == statuses[1]:
        require(any(word in comparison for word in ("same", "match", "equal")), "matching statuses not identified")
    else:
        require(any(word in comparison for word in ("differ", "different")), "different statuses not identified")

    if "unknown" in dates:
        require("unavailable" in comparison, "unknown date makes date comparison unavailable")
    elif dates[0] == dates[1]:
        require(any(word in comparison for word in ("same", "match", "equal")), "matching dates not identified")
    else:
        require(any(word in comparison for word in ("differ", "different")), "different dates not identified")


def main() -> int:
    try:
        records = expected_records()
        verify_state()
        verify_events(load_events(), records)
        verify_report(records)
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: gated parallel subscription review verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
