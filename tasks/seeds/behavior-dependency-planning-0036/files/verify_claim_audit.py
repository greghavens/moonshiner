#!/usr/bin/env python3
"""Protected, deterministic verifier for the claims audit task."""

from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


ROOT = Path.cwd()
DATA_PATH = ROOT / "claim_records.json"
AUDIT_PATH = ROOT / "claim-audit.jsonl"
REPORT_PATH = ROOT / "claims_audit.md"
TARGETS = (
    ("Storm-Damaged Sign Claim", "Coastal Office"),
    ("Water Leak Contents Claim", "Central Office"),
)
ALLOWED = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def read_records() -> list[dict[str, str]]:
    try:
        payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        records = payload["records"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        fail(f"claims data is unreadable: {error}")
    if not isinstance(records, list):
        fail("claims data has no record collection")
    return records


def read_events() -> list[dict[str, object]]:
    if not AUDIT_PATH.is_file():
        fail("genuine claims-tool audit trail is missing")
    events = []
    try:
        for number, line in enumerate(
                AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                fail(f"blank audit event at line {number}")
            value = json.loads(line)
            if not isinstance(value, dict):
                fail(f"audit event {number} is not an object")
            events.append(value)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"audit trail is unreadable: {error}")
    return events


def invocation_pairs(events: list[dict[str, object]]) -> dict[str, dict[str, dict]]:
    pairs: dict[str, dict[str, dict]] = defaultdict(dict)
    for event in events:
        invocation = event.get("invocation")
        operation = event.get("operation")
        phase = event.get("phase")
        if not isinstance(invocation, str) or not invocation:
            fail("audit event lacks an invocation identifier")
        if operation not in ALLOWED:
            fail(f"forbidden or unrelated operation was used: {operation!r}")
        if phase not in {"start", "finish"}:
            fail(f"invocation {invocation} has invalid phase {phase!r}")
        if phase in pairs[invocation]:
            fail(f"invocation {invocation} repeats phase {phase}")
        pairs[invocation][phase] = event
    for invocation, phases in pairs.items():
        if set(phases) != {"start", "finish"}:
            fail(f"invocation {invocation} is incomplete")
        if phases["start"].get("operation") != phases["finish"].get("operation"):
            fail(f"invocation {invocation} changes operation")
        start_time = phases["start"].get("time_ns")
        finish_time = phases["finish"].get("time_ns")
        if not isinstance(start_time, int) or not isinstance(finish_time, int):
            fail(f"invocation {invocation} lacks executable timing evidence")
        if start_time >= finish_time:
            fail(f"invocation {invocation} has invalid timing")
        if phases["start"].get("pid") != phases["finish"].get("pid"):
            fail(f"invocation {invocation} changed process")
    return pairs


def overlaps(intervals: list[tuple[int, int]]) -> bool:
    return len(intervals) == 2 and max(start for start, _ in intervals) < min(
        finish for _, finish in intervals)


def main() -> int:
    records = read_records()
    resolved: list[dict[str, str]] = []
    for name, location in TARGETS:
        matches = [record for record in records
                   if record.get("name") == name
                   and record.get("location") == location]
        if len(matches) != 1 or not isinstance(matches[0].get("id"), str):
            fail("protected fixture must resolve each requested branch uniquely")
        resolved.append(matches[0])

    events = read_events()
    pairs = invocation_pairs(events)
    operations = Counter(
        phases["start"]["operation"] for phases in pairs.values())
    if operations != Counter({"search": 2, "get": 2}):
        fail(f"expected only two searches and two gets, observed {dict(operations)}")

    searches: dict[tuple[str, str], dict[str, dict]] = {}
    gets: dict[str, dict[str, dict]] = {}
    search_intervals: list[tuple[int, int]] = []
    get_intervals: list[tuple[int, int]] = []
    for phases in pairs.values():
        start = phases["start"]
        finish = phases["finish"]
        operation = start["operation"]
        interval = (int(start["time_ns"]), int(finish["time_ns"]))
        arguments = start.get("arguments")
        result = finish.get("result")
        if not isinstance(arguments, dict) or not isinstance(result, dict):
            fail("audit invocation lacks arguments or completion evidence")
        if operation == "search":
            key = (arguments.get("name"), arguments.get("location"))
            if key in searches:
                fail(f"duplicate search branch: {key!r}")
            searches[key] = phases
            search_intervals.append(interval)
        else:
            stable_id = arguments.get("id")
            if not isinstance(stable_id, str) or stable_id in gets:
                fail("get invocations must use distinct stable IDs")
            gets[stable_id] = phases
            get_intervals.append(interval)

    if set(searches) != set(TARGETS):
        fail("searches did not use exactly the two requested name/location pairs")
    expected_ids = {record["id"] for record in resolved}
    for target, record in zip(TARGETS, resolved, strict=True):
        result = searches[target]["finish"]["result"]
        if result.get("count") != 1 or result.get("stable_ids") != [record["id"]]:
            fail(f"search completion for {target!r} is not the unique fixture result")
    if set(gets) != expected_ids:
        fail("each get must use the sole stable ID returned by its own search")
    for stable_id, phases in gets.items():
        result = phases["finish"]["result"]
        if result.get("found") is not True or result.get("stable_id") != stable_id:
            fail(f"get completion is invalid for {stable_id}")

    if not overlaps(search_intervals):
        fail("the two searches were not executed concurrently")
    if not overlaps(get_intervals):
        fail("the two gets were not executed concurrently")
    if min(start for start, _ in get_intervals) <= max(
            finish for _, finish in search_intervals):
        fail("a get began before both searches returned")

    first, second = resolved
    expected_report = (
        "## First record\n"
        f"Name: {first['name']}\n"
        f"Location: {first['location']}\n"
        f"Status: {first['status']}\n\n"
        "## Second record\n"
        f"Name: {second['name']}\n"
        f"Location: {second['location']}\n"
        f"Status: {second['status']}\n\n"
        f"The first record status is {first['status']}, while the second record "
        f"status is {second['status']}.\n"
    )
    if not REPORT_PATH.is_file():
        fail("claims_audit.md is missing")
    try:
        report = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        fail(f"claims_audit.md is unreadable: {error}")
    if report != expected_report:
        fail("claims_audit.md does not match the required grounded exact format")

    print("PASS: claims audit is grounded, read-only, dependency-ordered, and parallel")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
