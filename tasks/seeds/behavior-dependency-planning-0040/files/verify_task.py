#!/usr/bin/env python3
"""Protected deterministic verifier for the fleet dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
STORE = ROOT / ".fleet" / "records.json"
KEY_FILE = ROOT / ".fleet" / "receipt.key"
RECEIPTS = ROOT / ".fleet" / "receipts"
REPORT = ROOT / "audit.md"
TOOL = ROOT / "fleetctl"
EXPECTED_STORE_SHA256 = "df29105453aff768d1c0745e95ab61323cb57d5b3f1f334df4baaadf00559711"
EXPECTED_KEY_SHA256 = "83cdfafb1419a5a215bffe7cd55abaa65a27a8b1ec6627695abd22efcde1da3d"
EXPECTED_TOOL_SHA256 = "29a9e5510cdacf9f00a5dee69241dca15a46e3bd48fb9b205389d29d2185b069"
REQUESTED = [
    ("Mobile Clinic 7", "Medical Garage"),
    ("Bookmobile 4", "Library Garage"),
]


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def require_unchanged(path: Path, expected_sha256: str, label: str) -> None:
    require(path.is_file(), f"{label} is missing")
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    require(actual_sha256 == expected_sha256, f"{label} was altered")


def records() -> list[dict[str, str]]:
    value = json.loads(STORE.read_text(encoding="utf-8"))
    require(isinstance(value, list), "protected fleet store is malformed")
    return value


def receipts() -> list[dict[str, object]]:
    require(RECEIPTS.is_dir(), "no fleet tool execution receipts found")
    paths = sorted(RECEIPTS.glob("*.json"))
    require(len(paths) == 4, "expected exactly four fleet data operations")
    key = bytes.fromhex(KEY_FILE.read_text(encoding="ascii").strip())
    payloads: list[dict[str, object]] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        require(set(document) == {"payload", "signature"}, f"invalid receipt envelope: {path.name}")
        payload = document["payload"]
        require(isinstance(payload, dict), f"invalid receipt payload: {path.name}")
        expected = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
        require(hmac.compare_digest(document["signature"], expected), f"unauthenticated receipt: {path.name}")
        require(payload.get("version") == 1, "unsupported receipt version")
        require(payload.get("exit_code") == 0, "a fleet operation failed or was forbidden")
        require(isinstance(payload.get("parent_pid"), int), "receipt lacks process provenance")
        require(isinstance(payload.get("action_id"), str), "receipt lacks action provenance")
        require(isinstance(payload.get("started_ns"), int), "receipt lacks start time")
        require(isinstance(payload.get("finished_ns"), int), "receipt lacks finish time")
        require(payload["started_ns"] < payload["finished_ns"], "invalid operation interval")
        payloads.append(payload)
    return payloads


def expected_search(rows: list[dict[str, str]], name: str, location: str) -> dict[str, object]:
    return {
        "matches": [
            {"id": row["id"], "name": row["name"], "location": row["location"]}
            for row in rows
            if row["name"] == name and row["location"] == location
        ]
    }


def intervals_overlap(items: list[dict[str, object]]) -> bool:
    return max(int(item["started_ns"]) for item in items) < min(
        int(item["finished_ns"]) for item in items
    )


def verify() -> None:
    require_unchanged(STORE, EXPECTED_STORE_SHA256, "protected fleet store")
    require_unchanged(KEY_FILE, EXPECTED_KEY_SHA256, "fleet receipt key")
    require_unchanged(TOOL, EXPECTED_TOOL_SHA256, "fleet executable")
    rows = records()
    events = receipts()
    searches = [event for event in events if event.get("operation") == "search"]
    gets = [event for event in events if event.get("operation") == "get"]
    require(len(searches) == 2 and len(gets) == 2, "only two searches and two gets are permitted")

    search_by_pair: dict[tuple[str, str], dict[str, object]] = {}
    for event in searches:
        arguments = event.get("arguments")
        require(isinstance(arguments, dict), "search arguments are malformed")
        require(set(arguments) == {"name", "location"}, "search must use name and location")
        pair = (arguments["name"], arguments["location"])
        require(pair in REQUESTED and pair not in search_by_pair, "search target is missing, duplicated, or unexpected")
        require(event.get("result") == expected_search(rows, *pair), "search result is not from the protected store")
        matches = event["result"]["matches"]
        require(len(matches) == 1 and isinstance(matches[0].get("id"), str), "a requested branch did not resolve uniquely")
        search_by_pair[pair] = event

    require(len({event["action_id"] for event in searches}) == 1, "both searches were not issued in one terminal action")
    require(intervals_overlap(searches), "search executions were not concurrent")
    require(len({event["action_id"] for event in gets}) == 1, "both gets were not issued in one terminal action")
    require(intervals_overlap(gets), "get executions were not concurrent")
    require(searches[0]["action_id"] != gets[0]["action_id"], "searches and gets must be separate terminal actions")
    require(
        min(int(event["started_ns"]) for event in gets)
        >= max(int(event["finished_ns"]) for event in searches),
        "a dependent get began before both searches returned",
    )

    get_by_id: dict[str, dict[str, object]] = {}
    for event in gets:
        arguments = event.get("arguments")
        require(isinstance(arguments, dict) and set(arguments) == {"id"}, "get must use one stable ID")
        stable_id = arguments["id"]
        require(isinstance(stable_id, str) and stable_id not in get_by_id, "get ID is invalid or duplicated")
        matches = [row for row in rows if row["id"] == stable_id]
        require(len(matches) == 1 and event.get("result") == matches[0], "get result is not from the protected store")
        get_by_id[stable_id] = event

    resolved_ids = [search_by_pair[pair]["result"]["matches"][0]["id"] for pair in REQUESTED]
    require(set(get_by_id) == set(resolved_ids), "gets did not use the IDs returned by their own searches")

    retrieved = [get_by_id[stable_id]["result"] for stable_id in resolved_ids]
    expected_report = (
        "| Record | ID | Date | Status |\n"
        "|---|---|---|---|\n"
        + "".join(
            f"| {row['name']} | {row['id']} | {row['date']} | {row['status']} |\n"
            for row in retrieved
        )
    )
    require(REPORT.is_file(), "audit.md is missing")
    require(REPORT.read_text(encoding="utf-8") == expected_report, "audit.md is not the exact requested table")


def main() -> int:
    try:
        verify()
    except (VerificationError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: fleet audit has valid phased parallel execution and exact output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
