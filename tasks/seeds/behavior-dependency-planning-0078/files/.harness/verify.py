#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "subscription-audit.jsonl"
REPORT_PATH = ROOT / "account_review.txt"
AUDIT_KEY = b"moonshiner-pi-subscription-audit-0078-v1"

EXPECTED_HASHES = {
    "subscription-records": "ea7041be44db1f5b4ce628a3118824f79073e6901ad3fa5eebf0f7f60c886cd7",
    ".subscriptions/records.json": "99be5b2d5e406f1da4e6d11eaf16b7372f8d9329f2b2c74a2175ccb1dd018f08",
    "README.md": "52f3780e8477b99a282cf47bad745e4cf6df3d43b35fa4d261d9eaf4ad4e4546",
    ".gitignore": "fd4391c6fca49843a0fc482ba6001f0e9696bd5f530b7630d7056782ff9b448b",
}
EXPECTED_SEARCHES = {
    ("Field Team Satellite Subscription", "Programs"),
    ("Warehouse Scanner Subscription", "Fulfillment"),
}
EXPECTED_REPORT = (
    "Field Team Satellite Subscription | tel-178 | Programs | 2026-10-10 | pending-activation\n"
    "Warehouse Scanner Subscription | tel-578 | Fulfillment | 2026-10-11 | active\n"
    "Status comparison: Field Team Satellite Subscription is pending-activation; "
    "Warehouse Scanner Subscription is active, so the statuses differ.\n"
)
FORBIDDEN_OPERATIONS = {
    "list",
    "preferences",
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
            fail(f"protected subscription input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine subscription-registry execution trace was produced")
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
        fail("a forbidden subscription-registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required subscription-registry operations must succeed")

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
        fail("both subscription branches must resolve to exactly one stable ID")
    if any(
        not isinstance(event.get("result_ids"), list)
        or len(event["result_ids"]) != 1
        or not isinstance(event["result_ids"][0], str)
        or not event["result_ids"][0]
        for event in searches
    ):
        fail("a search did not return one auditable stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not two concurrent sibling Pi Bash-tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("gets did not use exactly the stable IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete subscription record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the gets were not two concurrent sibling Pi Bash-tool calls")
    search_groups = {event["process_group_id"] for event in searches}
    get_groups = {event["process_group_id"] for event in gets}
    if search_groups & get_groups:
        fail("search and get stages reused a Bash-tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("account_review.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("account_review.txt is not exactly the requested scoped audit")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: report and genuine Pi subscription-registry execution "
        "satisfy all constraints"
    )


if __name__ == "__main__":
    main()
