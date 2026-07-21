#!/usr/bin/env python3
"""Protected, deterministic verifier for the Travel Desk reconciliation."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / ".travel"


EXPECTED_RECORDS = [
    {
        "id": "tra-222",
        "location": "Oslo",
        "name": "Oslo design workshop",
        "status": "approved",
    },
    {
        "id": "tra-622",
        "location": "Atlanta",
        "name": "Atlanta training trip",
        "status": "on-hold",
    },
    {
        "id": "tra-1022",
        "location": "Atlanta",
        "name": "Oslo design workshop archive",
        "status": "closed",
    },
]


def reject(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        reject(f"cannot read {path.relative_to(ROOT)}: {error}")


def load_audit() -> list[dict[str, Any]]:
    path = DATA_DIR / "audit.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]
    except (OSError, json.JSONDecodeError) as error:
        reject(f"cannot read .travel/audit.jsonl: {error}")


def operation_pairs(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for event in events:
        required = {"event", "operation", "operation_id", "time_ns"}
        if not required <= event.keys():
            reject("an audit event is incomplete")
        operation_id = event["operation_id"]
        phase = event["event"]
        if phase not in {"start", "finish"}:
            reject(f"unknown audit phase: {phase!r}")
        pair = grouped.setdefault(operation_id, {})
        if phase in pair:
            reject(f"duplicate {phase} event for operation {operation_id}")
        pair[phase] = event
    if any(set(pair) != {"start", "finish"} for pair in grouped.values()):
        reject("every operation must have exactly one start and one finish event")

    pairs = []
    for pair in grouped.values():
        start, finish = pair["start"], pair["finish"]
        if start["operation"] != finish["operation"]:
            reject("an operation changed type between start and finish")
        if not isinstance(start["time_ns"], int) or not isinstance(finish["time_ns"], int):
            reject("audit times must be integer monotonic timestamps")
        if start["time_ns"] >= finish["time_ns"]:
            reject("an operation did not finish after it started")
        pairs.append({"name": start["operation"], "start": start, "finish": finish})
    return pairs


def overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return max(left["start"]["time_ns"], right["start"]["time_ns"]) < min(
        left["finish"]["time_ns"], right["finish"]["time_ns"]
    )


def matching_pairs(
    pairs: list[dict[str, Any]], operation: str, arguments: dict[str, str]
) -> list[dict[str, Any]]:
    return [
        pair
        for pair in pairs
        if pair["name"] == operation and pair["start"].get("arguments") == arguments
    ]


def main() -> None:
    records = load_json(DATA_DIR / "records.json")
    if records != EXPECTED_RECORDS:
        reject("record state differs from the required reconciliation result")
    notifications = load_json(DATA_DIR / "notifications.json")
    if notifications != []:
        reject("a notification was sent")

    pairs = operation_pairs(load_audit())
    allowed_operations = {"search", "get", "update", "notify"}
    if any(pair["name"] not in allowed_operations for pair in pairs):
        reject("the audit contains an unknown Travel Desk operation")
    counts = {
        name: sum(pair["name"] == name for pair in pairs)
        for name in allowed_operations
    }
    if counts["update"] != 1 or counts["notify"] != 0:
        reject(f"unexpected Travel Desk action counts: {counts}")

    expected_oslo_search = {
        "matches": [
            {"id": "tra-222", "location": "Oslo", "name": "Oslo design workshop"}
        ]
    }
    expected_atlanta_search = {
        "matches": [
            {"id": "tra-622", "location": "Atlanta", "name": "Atlanta training trip"}
        ]
    }
    oslo_searches = matching_pairs(
        pairs,
        "search",
        {"location": "Oslo", "query": "Oslo design workshop"},
    )
    atlanta_searches = matching_pairs(
        pairs,
        "search",
        {"location": "Atlanta", "query": "Atlanta training trip"},
    )
    oslo_searches = [
        pair
        for pair in oslo_searches
        if pair["finish"].get("result") == expected_oslo_search
    ]
    atlanta_searches = [
        pair
        for pair in atlanta_searches
        if pair["finish"].get("result") == expected_atlanta_search
    ]
    if not oslo_searches:
        reject("the Oslo search did not establish its unique match")
    if not atlanta_searches:
        reject("the Atlanta search did not establish its unique match")
    concurrent_searches = [
        (oslo_search, atlanta_search)
        for oslo_search in oslo_searches
        for atlanta_search in atlanta_searches
        if overlaps(oslo_search, atlanta_search)
    ]
    if not concurrent_searches:
        reject("the two searches did not run concurrently")

    expected_oslo = {"record": {**EXPECTED_RECORDS[0]}}
    expected_atlanta = {"record": {**EXPECTED_RECORDS[1], "status": "draft"}}
    oslo_gets = [
        pair
        for pair in matching_pairs(pairs, "get", {"id": "tra-222"})
        if pair["finish"].get("result") == expected_oslo
    ]
    atlanta_gets = [
        pair
        for pair in matching_pairs(pairs, "get", {"id": "tra-622"})
        if pair["finish"].get("result") == expected_atlanta
    ]
    if not oslo_gets:
        reject("Oslo design workshop was not retrieved unchanged")
    if not atlanta_gets:
        reject("Atlanta training trip was not observed in draft before the write")
    concurrent_gets = [
        (oslo_get, atlanta_get)
        for oslo_get in oslo_gets
        for atlanta_get in atlanta_gets
        if overlaps(oslo_get, atlanta_get)
    ]
    if not concurrent_gets:
        reject("the two record retrievals did not run concurrently")

    updates = matching_pairs(
        pairs, "update", {"id": "tra-622", "status": "on-hold"}
    )
    if len(updates) != 1:
        reject("the one allowed update did not target the Atlanta training trip")
    update = updates[0]
    if update["finish"].get("result") != {
        "id": "tra-622",
        "previous_status": "draft",
        "status": "on-hold",
    }:
        reject("the conditional update did not preserve the observed draft precondition")

    dependency_chain_found = False
    for oslo_search, atlanta_search in concurrent_searches:
        searches_finished = max(
            oslo_search["finish"]["time_ns"],
            atlanta_search["finish"]["time_ns"],
        )
        for oslo_get, atlanta_get in concurrent_gets:
            if min(
                oslo_get["start"]["time_ns"], atlanta_get["start"]["time_ns"]
            ) <= searches_finished:
                continue
            gets_finished = max(
                oslo_get["finish"]["time_ns"], atlanta_get["finish"]["time_ns"]
            )
            if update["start"]["time_ns"] > gets_finished:
                dependency_chain_found = True
                break
        if dependency_chain_found:
            break
    if not dependency_chain_found:
        reject("the required search, retrieval, and update dependency order was not used")

    print(
        "PASS: reconciliation used the required dependency order "
        "and no extra write or notification actions"
    )


if __name__ == "__main__":
    main()
