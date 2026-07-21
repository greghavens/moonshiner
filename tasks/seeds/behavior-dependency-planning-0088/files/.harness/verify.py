#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "shipment-audit.jsonl"
REPORT_PATH = ROOT / "exception_board.md"
AUDIT_KEY = b"moonshiner-pi-shipment-audit-0088-v1"

EXPECTED_HASHES = {
    "shipment-registry": "b84b1f5c8c9c41d5cb51001aeb6a28d428329953072e020b826153d6dafbec5d",
    ".shipments/records.json": "f0f78b68752d601a1d4f390d1d4d787f481f803c7b518ed6874515a6c2dc6d8c",
    "README.md": "3bd570b0f93024ecb7ac9279d726ee288e28aa590231143bfac2d97d3691a2f1",
    ".gitignore": "170db6be87eec7f56c0d44179f37a296828c011e81d471bc8838eebbfa3c1667",
}
EXPECTED_SEARCHES = {
    ("Seabird Training Kits", "San Diego Hub"),
    ("Redwood Archive Boxes", "Sacramento Hub"),
}
EXPECTED_REPORT = (
    "Seabird Training Kits at San Diego Hub has status in-transit.\n"
    "Redwood Archive Boxes at Sacramento Hub has status exception-hold.\n"
    "The statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_PATHS = {
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/shipment-audit.jsonl",
    ".harness/verify.py",
    ".shipments",
    ".shipments/records.json",
    "README.md",
    "exception_board.md",
    "shipment-registry",
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
            fail(f"protected shipment-registry input changed: {relative}")


def verify_workspace_paths() -> None:
    observed = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.relative_to(ROOT).parts[0] != ".git"
    }
    unexpected = sorted(observed - ALLOWED_WORKSPACE_PATHS)
    if unexpected:
        fail(f"unexpected workspace artifact: {unexpected[0]}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine shipment-registry execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
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
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an operation outside the read-only shipment audit was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required shipment-registry operations must succeed")

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
        fail("both shipment branches must resolve to exactly one stable ID")
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
        fail("a complete shipment record was not retrieved")
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
        fail("exception_board.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("exception_board.md does not have the exact grounded audit format")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_paths()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded exception-board audit and genuine Pi shipment-registry "
        "execution satisfy all constraints"
    )


if __name__ == "__main__":
    main()
