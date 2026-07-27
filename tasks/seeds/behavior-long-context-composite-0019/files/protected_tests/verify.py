#!/usr/bin/env python3
"""Protected acceptance checks for the application-register resolution."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "applications.sqlite3"
JOURNAL = ROOT / ".applicationctl" / "audit.json"
REPORT = ROOT / "application_record.md"
JOURNAL_KEY = b"moonshiner-application-register-0019-v1"
EXPECTED_DATABASE_SHA256 = "a712d2bf300db27b5a2fb513ce9a00f6fa6f6e712bef12d0b3116cd4d792cfa0"
TARGET_NAME = "Companion Animal License"
TARGET_LOCATION = "Aurora"
TARGET_ID = "pub-119"
EXPECTED_ROWS = (
    ("Field", "Value"),
    ("ID", "pub-119"),
    ("name", "Companion Animal License"),
    ("location", "Aurora"),
    ("status", "active"),
    ("date", "2026-04-14"),
)


class VerificationFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def load_journal() -> list[dict[str, Any]]:
    require(JOURNAL.is_file(), "missing applicationctl action journal")
    try:
        state = json.loads(JOURNAL.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationFailure("invalid applicationctl action journal") from error
    require(isinstance(state, dict), "invalid journal root")
    require(state.get("version") == 1, "unsupported journal version")
    events = state.get("events")
    require(isinstance(events, list), "invalid journal event list")
    require(len(events) == 3, "expected exactly help, search, and get executions")

    previous = "GENESIS"
    for position, event in enumerate(events, start=1):
        require(isinstance(event, dict), "invalid journal event")
        signature = event.get("signature")
        require(
            isinstance(signature, str) and len(signature) == 64,
            "invalid journal signature",
        )
        unsigned = dict(event)
        del unsigned["signature"]
        calculated = hmac.new(
            JOURNAL_KEY, canonical(unsigned), hashlib.sha256
        ).hexdigest()
        require(
            hmac.compare_digest(signature, calculated),
            "journal signature mismatch",
        )
        require(event.get("sequence") == position, "invalid journal sequence")
        require(event.get("previous") == previous, "broken journal chain")
        require(
            isinstance(event.get("parent_process"), int)
            and isinstance(event.get("process"), int)
            and isinstance(event.get("harness_action"), int),
            "missing execution identity",
        )
        previous = signature
    return events


def verify_database() -> None:
    digest = hashlib.sha256(DATABASE.read_bytes()).hexdigest()
    require(digest == EXPECTED_DATABASE_SHA256, "application register was modified")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        target = connection.execute(
            """
            SELECT stable_id, name, location, status, date
            FROM applications
            WHERE name = ? AND location = ?
            """,
            (TARGET_NAME, TARGET_LOCATION),
        ).fetchall()
        notifications = connection.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
    finally:
        connection.close()
    require(len(target) == 1, "protected register target is not unique")
    require(target[0]["stable_id"] == TARGET_ID, "protected target ID changed")
    require(target[0]["status"] == "active", "protected target is not active")
    require(notifications == 0, "a notification was sent")


def verify_actions(events: list[dict[str, Any]]) -> None:
    actions = [event.get("action") for event in events]
    require(
        actions == ["help", "search", "get"],
        "operations must be top-level help, exact search, then dependent get",
    )

    help_event, search_event, get_event = events
    require(help_event.get("input") == {}, "help execution had unexpected input")
    require(
        help_event.get("result") == {"scope": "top-level"},
        "top-level help was not inspected",
    )

    require(
        search_event.get("input")
        == {"name": TARGET_NAME, "location": TARGET_LOCATION},
        "search was not constrained by the exact name and location",
    )
    search_result = search_event.get("result")
    require(isinstance(search_result, dict), "search result was not recorded")
    matches = search_result.get("matches")
    require(search_result.get("count") == 1, "search did not return one match")
    require(
        isinstance(matches, list) and len(matches) == 1,
        "search match was not unique",
    )
    match = matches[0]
    require(isinstance(match, dict), "search match is invalid")
    returned_id = match.get("stable_id")
    require(
        returned_id == TARGET_ID
        and match.get("name") == TARGET_NAME
        and match.get("location") == TARGET_LOCATION,
        "search returned the wrong scoped application",
    )

    require(
        get_event.get("input") == {"stable_id": returned_id},
        "get did not use the stable ID returned by search",
    )
    require(
        len(
            {
                help_event.get("harness_action"),
                search_event.get("harness_action"),
                get_event.get("harness_action"),
            }
        )
        == 3,
        "help, search, and get were not separate harness shell-tool actions",
    )
    get_result = get_event.get("result")
    require(
        isinstance(get_result, dict) and get_result.get("found") is True,
        "full record retrieval failed",
    )
    record = get_result.get("record")
    require(isinstance(record, dict), "full record is missing")
    require(record.get("stable_id") == returned_id, "retrieved ID changed")
    require(record.get("name") == TARGET_NAME, "retrieved name changed")
    require(record.get("location") == TARGET_LOCATION, "retrieved location changed")
    require(record.get("status") == "active", "retrieved record is not active")
    require(record.get("date") == "2026-04-14", "retrieved date changed")


def verify_report() -> None:
    require(REPORT.is_file(), "application_record.md is missing")
    try:
        content = REPORT.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationFailure("application_record.md is not UTF-8") from error

    lines = content.splitlines()
    require(
        len(lines) == 7 and all(line.strip() for line in lines),
        "application_record.md must contain only the required compact table",
    )

    def cells(line: str) -> tuple[str, ...]:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return tuple(cell.strip() for cell in stripped.split("|"))

    delimiter = cells(lines[1])
    require(
        len(delimiter) == 2
        and all(re.fullmatch(r":?-{3,}:?", cell) for cell in delimiter),
        "application_record.md has an invalid Markdown table delimiter",
    )
    actual_rows = (cells(lines[0]), *(cells(line) for line in lines[2:]))
    require(
        actual_rows == EXPECTED_ROWS,
        "application_record.md has incorrect columns, row order, or values",
    )


def main() -> int:
    try:
        verify_database()
        events = load_journal()
        verify_actions(events)
        verify_report()
    except (OSError, sqlite3.Error, VerificationFailure) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact scoped search, dependent full retrieval, and read-only table verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
