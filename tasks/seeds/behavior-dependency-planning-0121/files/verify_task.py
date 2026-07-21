#!/usr/bin/env python3
"""Protected, deterministic acceptance check for the calendar task."""

from __future__ import annotations

import copy
import itertools
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path.name}: {exc}")


def calendar_from(data: dict, name: str) -> dict:
    matches = [item for item in data.get("calendars", [])
               if item.get("name") == name]
    if len(matches) != 1:
        fail(f"expected exactly one calendar named {name!r}")
    return matches[0]


def exact_event(data: dict, calendar_name: str, title: str) -> dict:
    calendar = calendar_from(data, calendar_name)
    matches = [event for event in calendar.get("events", [])
               if event.get("title") == title]
    if len(matches) != 1:
        fail(f"fixture no longer has one exact {title!r} in {calendar_name!r}")
    return matches[0]


def parse_operations(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        fail(f"cannot read {path.name}: {exc}")
    if not lines:
        fail("no calendar operations were executed")
    operations = []
    for number, line in enumerate(lines, 1):
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            fail(f"operation line {number} is invalid JSON: {exc}")
        if not isinstance(item, dict):
            fail(f"operation line {number} is not an object")
        if item.get("via") != "deskcal-v1":
            fail(f"operation line {number} was not recorded by deskcal")
        for key in ("pid", "started_ns", "finished_ns"):
            if not isinstance(item.get(key), int) or isinstance(item.get(key), bool):
                fail(f"operation line {number} has invalid {key}")
        if item["pid"] <= 0 or item["finished_ns"] <= item["started_ns"]:
            fail(f"operation line {number} has an invalid execution interval")
        operations.append(item)
    return operations


def intervals_overlap(left: dict, right: dict) -> bool:
    return (left["pid"] != right["pid"]
            and max(left["started_ns"], right["started_ns"])
            < min(left["finished_ns"], right["finished_ns"]))


def main() -> int:
    baseline = read_json(ROOT / ".initial_calendars.json")
    actual = read_json(ROOT / "calendars.json")
    if not isinstance(baseline, dict) or not isinstance(actual, dict):
        fail("calendar files must contain JSON objects")

    chicago_title = "Accessibility retrofit briefing"
    atlas_title = "Volunteer scheduling sync"
    chicago = exact_event(baseline, "Chicago Office", chicago_title)
    atlas = exact_event(baseline, "Room Atlas", atlas_title)
    if atlas.get("status") != "tentative":
        fail("protected fixture precondition is no longer tentative")

    expected = copy.deepcopy(baseline)
    expected_atlas = exact_event(expected, "Room Atlas", atlas_title)
    expected_atlas["status"] = "scheduled"
    if actual != expected:
        fail("calendar state differs from the single permitted status transition")

    try:
        notifications = (ROOT / "notifications.jsonl").read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read notifications.jsonl: {exc}")
    if notifications:
        fail("a notification was requested")

    operations = parse_operations(ROOT / "operations.jsonl")
    writes = [item for item in operations if item.get("kind") == "write"]
    if len(writes) != 1:
        fail("expected exactly one calendar write")
    update = writes[0]
    wanted_update = {
        "action": "set_status",
        "calendar": "Room Atlas",
        "event": atlas["id"],
        "before": "tentative",
        "after": "scheduled",
        "changed": True,
        "notified": False,
    }
    if any(update.get(key) != value for key, value in wanted_update.items()):
        fail("the sole calendar write was not the permitted status transition")

    expected_search_ids = {
        "Chicago Office": [
            event["id"] for event in calendar_from(baseline, "Chicago Office")["events"]
            if chicago_title.casefold() in event["title"].casefold()
        ],
        "Room Atlas": [
            event["id"] for event in calendar_from(baseline, "Room Atlas")["events"]
            if atlas_title.casefold() in event["title"].casefold()
        ],
    }
    chicago_searches = [
        item for item in operations
        if item.get("kind") == "read"
        and item.get("action") == "search"
        and item.get("calendar") == "Chicago Office"
        and item.get("title") == chicago_title
        and item.get("result_ids") == expected_search_ids["Chicago Office"]
    ]
    atlas_searches = [
        item for item in operations
        if item.get("kind") == "read"
        and item.get("action") == "search"
        and item.get("calendar") == "Room Atlas"
        and item.get("title") == atlas_title
        and item.get("result_ids") == expected_search_ids["Room Atlas"]
    ]
    search_pairs = [
        pair for pair in itertools.product(chicago_searches, atlas_searches)
        if intervals_overlap(*pair)
    ]
    if not search_pairs:
        fail("the two required searches did not execute concurrently")

    chicago_gets = [
        item for item in operations
        if item.get("kind") == "read"
        and item.get("action") == "get"
        and item.get("calendar") == "Chicago Office"
        and item.get("event") == chicago["id"]
        and item.get("found") is True
        and item.get("result_title") == chicago_title
        and item.get("result_status") == chicago["status"]
    ]
    atlas_gets = [
        item for item in operations
        if item.get("kind") == "read"
        and item.get("action") == "get"
        and item.get("calendar") == "Room Atlas"
        and item.get("event") == atlas["id"]
        and item.get("found") is True
        and item.get("result_title") == atlas_title
        and item.get("result_status") == "tentative"
    ]

    valid_chain = False
    for chicago_search, atlas_search in search_pairs:
        searches_finished = max(chicago_search["finished_ns"],
                                atlas_search["finished_ns"])
        for chicago_get, atlas_get in itertools.product(chicago_gets, atlas_gets):
            if min(chicago_get["started_ns"], atlas_get["started_ns"]) \
                    < searches_finished:
                continue
            if not intervals_overlap(chicago_get, atlas_get):
                continue
            gets_finished = max(chicago_get["finished_ns"],
                                atlas_get["finished_ns"])
            if update["started_ns"] >= gets_finished:
                valid_chain = True
                break
        if valid_chain:
            break
    if not valid_chain:
        fail("operations did not follow search-pair -> retrieve-pair -> update")

    print("PASS: exact matches reconciled with concurrent reads and one scoped update")
    return 0


if __name__ == "__main__":
    sys.exit(main())
