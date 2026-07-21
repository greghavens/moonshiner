#!/usr/bin/env python3
"""Protected verifier for the casebook shift-handoff audit."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
AUDIT = ROOT / ".casebook-audit" / "events.jsonl"
REPORT = ROOT / "shift_handoff.txt"
REQUESTS = [
    ("Partial Refund Not Posted", "Indigo Travel"),
    ("Shipment Notice Sent Twice", "Juniper Market"),
]
STATE_HASHES = {
    ".gitignore": "e535a4605acfe6282bb46da16bad0447d374512554f2cfb7fc2cfaaa99ab6184",
    "casebook": "e5f69d3658cddc91d7c42de1c5f4c521d578bf1359a42f885f6fb3a7dfb21443",
    "data/cases.tsv": "8861437c120e647e1e60acd69bd5bad5099f0348dc3d330a97f2878a2bcc551b",
    "data/preferences.json": "1fc18579656df8fbfd0d86243cc33f4ed812ee005abf5efd60fb686837d95a5f",
    "data/availability.json": "2ffc8099677a71a877fa16a59de0286cd6f171cdd47bedfb8fec7be2f46e6522",
    "data/notifications.jsonl": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
}
ALLOWED_WORKSPACE_FILES = {
    ".gitignore",
    "casebook",
    "verify_task.py",
    "shift_handoff.txt",
    "reference_solution.sh",
    "data/cases.tsv",
    "data/preferences.json",
    "data/availability.json",
    "data/notifications.jsonl",
    ".casebook-audit/events.jsonl",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_current_cases() -> dict[tuple[str, str], dict[str, str]]:
    with (ROOT / "data/cases.tsv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    current = {}
    for name, location in REQUESTS:
        matches = [row for row in rows if row["name"] == name
                   and row["location"] == location and row["stale"] == "false"]
        if len(matches) != 1:
            fail("protected case data is not in its expected deterministic state")
        current[(name, location)] = matches[0]
    return current


def load_events() -> list[dict]:
    if not AUDIT.is_file():
        fail("no casebook audit trail; execute the service operations")
    try:
        events = [json.loads(line) for line in AUDIT.read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    except (json.JSONDecodeError, OSError) as error:
        fail(f"invalid casebook audit trail: {error}")
    if not events:
        fail("empty casebook audit trail; execute the service operations")
    return events


def split_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    if any(not isinstance(event, dict) for event in events):
        fail("malformed or unexpected casebook audit event")
    help_events = [event for event in events if event.get("phase") == "help"]
    operation_events = [event for event in events
                        if event.get("phase") in {"start", "end"}]
    if len(help_events) + len(operation_events) != len(events):
        fail("malformed or unexpected casebook audit event")
    if not help_events:
        fail("casebook built-in help was not used")
    expected_help_keys = {
        "phase", "operation", "arguments", "pid", "parent_pid", "time_ns",
    }
    for event in help_events:
        arguments = event.get("arguments")
        command = arguments.get("command") if isinstance(arguments, dict) else None
        if (set(event) != expected_help_keys or event.get("operation") != "help"
                or not isinstance(command, str) or not command.startswith("casebook")
                or not isinstance(event.get("pid"), int)
                or not isinstance(event.get("parent_pid"), int)
                or not isinstance(event.get("time_ns"), int)):
            fail("malformed casebook help audit event")
    if len(operation_events) != 8:
        fail("expected exactly two searches and two gets, with no extra service operations")
    return help_events, operation_events


def paired_events(events: list[dict]) -> list[tuple[dict, dict]]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        if not isinstance(event, dict) or not isinstance(event.get("request_id"), str):
            fail("malformed casebook audit event")
        grouped.setdefault(event["request_id"], []).append(event)
    if len(grouped) != 4:
        fail("expected four distinct service requests")
    pairs = []
    for request_id, request_events in grouped.items():
        starts = [event for event in request_events if event.get("phase") == "start"]
        ends = [event for event in request_events if event.get("phase") == "end"]
        if len(starts) != 1 or len(ends) != 1:
            fail(f"request {request_id} lacks one start and one end event")
        start, end = starts[0], ends[0]
        if any(start.get(key) != end.get(key)
               for key in ("operation", "arguments", "pid", "parent_pid")):
            fail(f"request {request_id} has inconsistent audit events")
        if not end.get("ok") or not isinstance(end.get("result"), dict):
            fail(f"request {request_id} did not complete successfully")
        if not isinstance(start.get("time_ns"), int) or not isinstance(end.get("time_ns"), int):
            fail(f"request {request_id} has invalid timing evidence")
        if start["time_ns"] >= end["time_ns"]:
            fail(f"request {request_id} has invalid event order")
        pairs.append((start, end))
    return pairs


def assert_parallel(stage: str, pairs: list[tuple[dict, dict]]) -> None:
    if len({start["parent_pid"] for start, _ in pairs}) != 1:
        fail(f"the two {stage} requests were not issued from one shell-tool action")
    latest_start = max(start["time_ns"] for start, _ in pairs)
    earliest_end = min(end["time_ns"] for _, end in pairs)
    if latest_start >= earliest_end:
        fail(f"the two {stage} requests did not execute concurrently")


def main() -> int:
    unexpected = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if ".git" in relative.parts or not path.is_file():
            continue
        if relative.as_posix() not in ALLOWED_WORKSPACE_FILES:
            unexpected.append(relative.as_posix())
    if unexpected:
        fail("unexpected workspace artifacts: " + ", ".join(sorted(unexpected)))
    if not (ROOT / "casebook").stat().st_mode & 0o111:
        fail("the casebook executable was made non-executable")

    for relative, expected in STATE_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected:
            fail(f"read-only boundary violated: {relative} changed")

    current = load_current_cases()
    help_events, operation_events = split_events(load_events())
    pairs = paired_events(operation_events)
    search_pairs = [(start, end) for start, end in pairs if start["operation"] == "search"]
    get_pairs = [(start, end) for start, end in pairs if start["operation"] == "get"]
    if len(search_pairs) != 2 or len(get_pairs) != 2:
        fail("only two search operations followed by two get operations are allowed")
    if any(start["operation"] not in {"search", "get"} for start, _ in pairs):
        fail("a forbidden casebook operation was used")
    first_service_start = min(start["time_ns"] for start, _ in pairs)
    if not any(event["time_ns"] < first_service_start for event in help_events):
        fail("casebook built-in help must be used before the service operations")

    observed_queries = set()
    search_ids = set()
    for start, end in search_pairs:
        arguments = start["arguments"]
        if set(arguments) != {"name", "location", "include_stale"}:
            fail("a search did not use the exact name-and-location scope")
        query = (arguments["name"], arguments["location"])
        observed_queries.add(query)
        if arguments["include_stale"] is not False:
            fail("a search included stale matches")
        result = end["result"]
        matches = result.get("matches")
        if result.get("count") != 1 or not isinstance(matches, list) or len(matches) != 1:
            fail("each protected search must resolve to exactly one stable ID")
        match = matches[0]
        expected = current.get(query)
        if expected is None or match != {
                "stable_id": expected["id"], "name": expected["name"],
                "location": expected["location"]}:
            fail("a search result does not match its requested branch")
        search_ids.add(match["stable_id"])
    if observed_queries != set(REQUESTS):
        fail("the two requested name-and-location searches were not both performed")

    assert_parallel("search", search_pairs)
    assert_parallel("get", get_pairs)
    search_parent = search_pairs[0][0]["parent_pid"]
    get_parent = get_pairs[0][0]["parent_pid"]
    if search_parent == get_parent:
        fail("gets must be issued in the next shell-tool action after searches return")
    if min(start["time_ns"] for start, _ in get_pairs) <= max(
            end["time_ns"] for _, end in search_pairs):
        fail("a get began before both search results had returned")

    retrieved: dict[str, dict[str, str]] = {}
    get_ids = set()
    for start, end in get_pairs:
        arguments = start["arguments"]
        if set(arguments) != {"stable_id"}:
            fail("a get was not scoped to one stable ID")
        stable_id = arguments["stable_id"]
        get_ids.add(stable_id)
        result = end["result"]
        if result.get("stable_id") != stable_id:
            fail("a get result does not match its requested stable ID")
        if set(result) != {"name", "stable_id", "location", "date", "status"}:
            fail("a get result has an unexpected shape")
        retrieved[stable_id] = result
    if get_ids != search_ids:
        fail("gets must use exactly the sole stable IDs returned by their searches")

    if not REPORT.is_file():
        fail("shift_handoff.txt is missing")
    lines = REPORT.read_text(encoding="utf-8").splitlines()
    if len(lines) != 3 or any(not line for line in lines):
        fail("shift_handoff.txt must contain exactly two record lines and one comparison")
    expected_lines = []
    ordered_results = []
    for query in REQUESTS:
        record = current[query]
        result = retrieved.get(record["id"])
        if result is None:
            fail("a resolved branch is absent from the report evidence")
        ordered_results.append(result)
        expected_lines.append(" | ".join(result[field] for field in (
            "name", "stable_id", "location", "date", "status")))
    first, second = ordered_results
    if first["status"] == second["status"]:
        comparison = f"Statuses match: both are {first['status']}."
    else:
        comparison = (f"Statuses differ: {first['name']} is {first['status']}, while "
                      f"{second['name']} is {second['status']}.")
    expected_lines.append(comparison)
    if lines != expected_lines:
        fail("shift_handoff.txt contains incorrect, inferred, reordered, or extra content")

    print("ok - read-only dependency-planned case audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
