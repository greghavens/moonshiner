#!/usr/bin/env python3
"""Protected deterministic verification for the registrar audit."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "audit.md"
TRACE = ROOT / ".registrar" / "audit.json"
CATALOG = ROOT / "catalog.sqlite"

EXPECTED_RECORDS = {
    "edu-172": {
        "course_date": "2026-10-17",
        "location": "Online Cohort",
        "name": "Accessible Document Design",
        "stable_id": "edu-172",
        "status": "enrollment-open",
    },
    "edu-572": {
        "course_date": "2026-10-18",
        "location": "South Campus",
        "name": "Fleet Safety Fundamentals",
        "stable_id": "edu-572",
        "status": "approval-required",
    },
}
EXPECTED_SEARCHES = {
    ("Accessible Document Design", "Online Cohort"): "edu-172",
    ("Fleet Safety Fundamentals", "South Campus"): "edu-572",
}
CATALOG_SNAPSHOT = [
    ("edu-172", "Accessible Document Design", "Online Cohort", "enrollment-open", "2026-10-17"),
    ("edu-572", "Fleet Safety Fundamentals", "South Campus", "approval-required", "2026-10-18"),
    ("edu-872", "Fleet Safety Fundamentals", "South Campus Online", "archived", "2025-10-18"),
    ("edu-972", "Accessible Document Design", "Online Cohort Archive", "archived", "2025-10-17"),
]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def verify_catalog() -> None:
    connection = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    rows = connection.execute(
        """
        SELECT stable_id, name, location, status, course_date
        FROM courses
        ORDER BY stable_id
        """
    ).fetchall()
    connection.close()
    require(rows == CATALOG_SNAPSHOT, "the registrar catalog changed")


def verify_report() -> None:
    require(REPORT.is_file(), "audit.md is missing")
    text = REPORT.read_text(encoding="utf-8")
    lines = text.splitlines()
    require(len(lines) == 3, "audit.md must contain exactly three lines")
    require(all(line.startswith("- ") for line in lines), "all three lines must be Markdown bullets")
    require(all(line.strip() == line and line[2:].strip() for line in lines), "bullets must be nonempty with no surrounding whitespace")

    first, second, comparison = [line.casefold() for line in lines]
    for token in ("accessible document design", "online cohort", "edu-172", "enrollment-open"):
        require(token in first, f"first bullet is missing {token!r}")
    for token in ("fleet safety fundamentals", "south campus", "edu-572", "approval-required"):
        require(token in second, f"second bullet is missing {token!r}")
    for token in ("edu-172", "enrollment-open", "edu-572", "approval-required"):
        require(token in comparison, f"comparison bullet is missing {token!r}")
    require(any(word in comparison for word in ("different", "differ", "versus", "vs.")), "comparison bullet must state that the statuses differ")
    lowered = text.casefold()
    for forbidden in ("edu-872", "edu-972", "south campus online", "online cohort archive"):
        require(forbidden not in lowered, "audit.md exposes a distractor record")


def interval_overlaps(calls: list[dict[str, Any]]) -> bool:
    return max(item["started_ns"] for item in calls) < min(item["finished_ns"] for item in calls)


def verify_trace() -> None:
    require(TRACE.is_file(), "no audited registrar operations were recorded")
    trace = json.loads(TRACE.read_text(encoding="utf-8"))
    require(trace.get("schema_version") == 1, "unexpected audit trace schema")
    calls = trace.get("calls")
    require(isinstance(calls, list) and len(calls) == 5, "the help call and exactly four record operations are required")
    require([item.get("call_id") for item in calls] == [1, 2, 3, 4, 5], "registrar calls are not a single clean sequence")
    require(all(item.get("outcome") == "ok" for item in calls), "a registrar call failed or was prohibited")
    help_call = calls[0]
    require(help_call.get("operation") == "help", "the interface must be discovered before record operations")
    require(help_call.get("arguments") == {}, "the required top-level help command was not used")
    require(help_call.get("result") == {"displayed": True}, "the registrar help was not displayed")
    require([item.get("operation") for item in calls[1:3]] == ["search", "search"], "the first wave must contain only the two searches")
    require([item.get("operation") for item in calls[3:]] == ["get", "get"], "the second wave must contain only the two gets")

    searches = calls[1:3]
    gets = calls[3:]
    require(len({item["action_identity"] for item in searches}) == 1, "both searches must be issued by one Bash action")
    require(len({item["action_identity"] for item in gets}) == 1, "both gets must be issued by one Bash action")
    require(help_call["action_identity"] != searches[0]["action_identity"], "help and searches must be separate Bash actions")
    require(searches[0]["action_identity"] != gets[0]["action_identity"], "searches and gets must be separate successive Bash actions")
    require(help_call["finished_ns"] < min(item["started_ns"] for item in searches), "searches began before the help command returned")
    require(interval_overlaps(searches), "the two searches did not execute concurrently")
    require(interval_overlaps(gets), "the two gets did not execute concurrently")
    require(max(item["finished_ns"] for item in searches) < min(item["started_ns"] for item in gets), "gets began before both searches returned")

    observed_searches: dict[tuple[str, str], str] = {}
    for item in searches:
        arguments = item.get("arguments", {})
        key = (arguments.get("name"), arguments.get("location"))
        result = item.get("result", {})
        matches = result.get("matches", [])
        require(result.get("match_count") == 1 and len(matches) == 1, "each requested search must resolve uniquely")
        stable_id = matches[0].get("stable_id")
        require(matches[0].get("name") == key[0] and matches[0].get("location") == key[1], "search result does not match its requested branch")
        observed_searches[key] = stable_id
    require(observed_searches == EXPECTED_SEARCHES, "the exact two name-and-location searches were not performed")

    observed_gets: dict[str, dict[str, str]] = {}
    for item in gets:
        stable_id = item.get("arguments", {}).get("stable_id")
        result = item.get("result")
        require(stable_id in EXPECTED_RECORDS, "a get used an ID not returned by a requested unique search")
        require(result == EXPECTED_RECORDS[stable_id], "get result is incomplete or does not match the stable record")
        observed_gets[stable_id] = result
    require(observed_gets == EXPECTED_RECORDS, "both uniquely resolved branches must be retrieved")


def main() -> int:
    try:
        verify_catalog()
        verify_trace()
        verify_report()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: read-only registrar audit, dependency order, parallel waves, and exact report format verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
