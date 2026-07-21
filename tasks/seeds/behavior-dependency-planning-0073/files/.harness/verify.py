#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "library-audit.jsonl"
REPORT_PATH = ROOT / "circulation_audit.txt"
AUDIT_KEY = b"moonshiner-pi-library-audit-0073-v1"

EXPECTED_HASHES = {
    "library-records": "ecaa8d3d7286f232bc9fd3d6503dfd4674cf304c100bddbe74c54c38c6d4cc81",
    ".library/titles.json": "e4753d72402a9c419926e73ceb39fc5c159e732761e439a07414ec746f5a5e64",
    "README.md": "4f260b3654192cc8bcef04876b14aeb17089bcd75b70b2ba99965b263beffb7e",
    ".gitignore": "e66f700830af394d29579fbf527e12cf6dce02b01eca4cd7a8a6137922ad832f",
}
EXPECTED_SEARCHES = {
    ("Winter Kitchens Oral History", "South Branch"),
    ("Practical Archive Housing", "Central Branch"),
}
EXPECTED_RECORD_LINES = [
    "Winter Kitchens Oral History at South Branch (lib-173) has status on-hold.",
    "Practical Archive Housing at Central Branch (lib-573) has status checked-out.",
]
EXPECTED_COMPARISON = (
    "The statuses differ: Winter Kitchens Oral History is on-hold, while "
    "Practical Archive Housing is checked-out."
)
FORBIDDEN_OPERATIONS = {
    "list",
    "profile",
    "availability",
    "create",
    "update",
    "cancel",
    "notify",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected library input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine library-registry execution trace was produced")
    events = []
    for number, raw in enumerate(AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return sorted(events, key=lambda item: item.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first["parent_pid"] == second["parent_pid"]
        and first["parent_start_ticks"] == second["parent_start_ticks"]
        and first["parent_start_ticks"] != "unavailable"
    )


def separate_tool_calls(first: dict, second: dict) -> bool:
    return (
        isinstance(first.get("process_group_id"), int)
        and isinstance(second.get("process_group_id"), int)
        and first["process_group_id"] != second["process_group_id"]
        and first.get("process_pid") == first["process_group_id"]
        and second.get("process_pid") == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required registry operations must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry-execution action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry-execution action must contain only both gets")

    observed_searches = {
        (event.get("name"), event.get("location")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-location searches were not executed")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both library branches must resolve to exactly one stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not two concurrent sibling Pi shell-tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("gets did not use exactly the stable IDs returned by the searches")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the gets were not two concurrent sibling Pi shell-tool calls")
    search_groups = {event["process_group_id"] for event in searches}
    get_groups = {event["process_group_id"] for event in gets}
    if search_groups & get_groups:
        fail("search and get stages reused a shell-tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("circulation_audit.txt is missing")
    text = REPORT_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    if text != "\n".join(lines) + "\n" or len(lines) != 3 or any(not line for line in lines):
        fail("audit must contain exactly three newline-terminated nonblank lines")
    if lines[:2] != EXPECTED_RECORD_LINES:
        fail("record sentences are incomplete, out of order, or contain extra fields")
    if lines[2] != EXPECTED_COMPARISON:
        fail("the third sentence does not compare only the two retrieved statuses")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: report and genuine Pi registry execution satisfy all constraints")


if __name__ == "__main__":
    main()
