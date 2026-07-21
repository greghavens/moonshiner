#!/usr/bin/env python3
"""Protected, deterministic verifier for the project status audit."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "data" / "projects.sqlite3"
DIGEST = ROOT / "data" / "projects.sqlite3.sha256"
REPORT = ROOT / "status_audit.md"
EVENTS = ROOT / ".pytest_cache" / "project_audit" / "events.jsonl"
TARGETS = (
    ("Policy handbook review", "Beacon Program"),
    ("Autumn newsletter draft", "Clover Program"),
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def value(record: dict[str, object], field: str) -> str:
    item = record.get(field)
    return "unknown" if item is None or item == "" else str(item)


def records_from_database() -> list[dict[str, object]]:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        records = []
        for name, location in TARGETS:
            rows = connection.execute(
                """
                SELECT id, name, location, status
                  FROM projects
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            if len(rows) != 1:
                fail("protected project data no longer has one unique target match")
            records.append(dict(rows[0]))
        if connection.execute("SELECT count(*) FROM notifications").fetchone()[0] != 0:
            fail("a notification was created")
        return records
    finally:
        connection.close()


def report_groups_values(
    report: str,
    record: dict[str, object],
    other: dict[str, object],
) -> bool:
    required = [value(record, field) for field in ("id", "name", "location", "status")]
    lines = report.splitlines()
    if any(all(item in line for item in required) for line in lines):
        return True
    for block in re.split(r"\n\s*\n", report):
        if all(item in block for item in required) and all(
            value(other, field) not in block for field in ("id", "name")
        ):
            return True
    for index, line in enumerate(lines):
        if value(record, "name") not in line:
            continue
        section = []
        for following in lines[index:]:
            if section and value(other, "name") in following:
                break
            section.append(following)
        if all(item in "\n".join(section) for item in required):
            return True
    return False


def verify_report(report: str, records: list[dict[str, object]]) -> None:
    first, second = records
    if not report_groups_values(report, first, second):
        fail("status_audit.md does not group all returned fields for the first record")
    if not report_groups_values(report, second, first):
        fail("status_audit.md does not group all returned fields for the second record")

    first_status = value(first, "status")
    second_status = value(second, "status")
    relation_words = (
        r"\b(?:matches?|same|equal|identical)\b"
        if first_status == second_status
        else r"\b(?:differs?|different|unequal|whereas|while|versus|vs\.?|"
        r"do not match|not (?:the )?same)\b"
    )
    comparison_found = False
    for block in [*report.splitlines(), *re.split(r"\n\s*\n", report)]:
        if (
            re.search(relation_words, block, flags=re.IGNORECASE)
            and (
                (first_status in block and second_status in block)
                or "status" in block.lower()
            )
        ):
            comparison_found = True
            break
    if not comparison_found:
        fail("status_audit.md does not directly compare the two returned statuses")

    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True) as connection:
        unrelated = connection.execute(
            "SELECT id, name, location FROM projects WHERE NOT "
            "((name = ? AND location = ?) OR (name = ? AND location = ?))",
            (*TARGETS[0], *TARGETS[1]),
        ).fetchall()
    for unrelated_id, unrelated_name, unrelated_location in unrelated:
        id_pattern = rf"(?<![\w-]){re.escape(str(unrelated_id))}(?![\w-])"
        grouped_name_and_location = any(
            str(unrelated_name) in line and str(unrelated_location) in line
            for line in report.splitlines()
        )
        if re.search(id_pattern, report) or grouped_name_and_location:
            fail("status_audit.md reports an unrelated record")


def load_events() -> list[dict[str, object]]:
    if not EVENTS.is_file():
        fail("no projectsctl execution history found")
    events = []
    for line_number, line in enumerate(EVENTS.read_text().splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            fail(f"invalid projectsctl execution event on line {line_number}")
        if not isinstance(event, dict):
            fail(f"invalid projectsctl execution event on line {line_number}")
        events.append(event)
    return events


def intervals_overlap(events: list[dict[str, object]]) -> bool:
    return max(int(event["started_ns"]) for event in events) < min(
        int(event["finished_ns"]) for event in events
    )


def verify_execution(events: list[dict[str, object]], records: list[dict[str, object]]) -> None:
    if len(events) != 5:
        fail("expected one help, two searches, and two retrievals")
    if any(event.get("ok") is not True for event in events):
        fail("every required projectsctl operation must succeed")

    helps = [event for event in events if event.get("operation") == "help"]
    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    if len(helps) != 1 or len(searches) != 2 or len(gets) != 2:
        fail("only one help, two search, and two get operations are allowed")
    if int(helps[0]["finished_ns"]) > min(
        int(event["started_ns"]) for event in searches
    ):
        fail("the live help did not finish before the searches started")

    observed_targets = {
        (
            str(event.get("arguments", {}).get("query")),
            str(event.get("arguments", {}).get("location")),
        )
        for event in searches
    }
    if observed_targets != set(TARGETS):
        fail("the two searches did not use the requested record and program pairs")
    if not intervals_overlap(searches):
        fail("the independent searches did not execute concurrently")
    if not intervals_overlap(gets):
        fail("the two retrievals did not execute concurrently")
    if len({event.get("parent_pid") for event in searches}) != 1:
        fail("the searches were not launched by one shell action")
    if len({event.get("parent_pid") for event in gets}) != 1:
        fail("the retrievals were not launched by one shell action")
    if min(int(event["started_ns"]) for event in gets) < max(
        int(event["finished_ns"]) for event in searches
    ):
        fail("a retrieval started before both searches had returned")

    search_ids: set[str] = set()
    for event in searches:
        result_ids = event.get("result_ids")
        if not isinstance(result_ids, list) or len(result_ids) != 1:
            fail("each search must return exactly one stable ID")
        search_ids.add(str(result_ids[0]))
    retrieved_ids = {
        str(event.get("arguments", {}).get("id")) for event in gets
    }
    expected_ids = {value(record, "id") for record in records}
    if search_ids != expected_ids or retrieved_ids != search_ids:
        fail("retrieval IDs must be exactly the unique IDs returned by the searches")


def main() -> int:
    expected_digest = DIGEST.read_text().strip()
    actual_digest = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    if actual_digest != expected_digest:
        fail("the protected project database was modified")

    records = records_from_database()
    verify_execution(load_events(), records)
    if not REPORT.is_file():
        fail("status_audit.md is missing")
    verify_report(REPORT.read_text(), records)
    print("PASS: concurrent dependency-ordered read-only audit verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
