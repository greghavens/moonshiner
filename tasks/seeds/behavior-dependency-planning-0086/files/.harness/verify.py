#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "order-audit.jsonl"
REPORT_PATH = ROOT / "procurement_audit.md"
AUDIT_KEY = b"moonshiner-pi-order-audit-0086-v1"

EXPECTED_HASHES = {
    "order-registry": "65b94eae3382dd14f19b8ca1d5720849e81ddb1431ca3b49c9977bd93342a71d",
    ".orders/records.json": "5eb5373d38ca46e6a03158031b8b39f507963466e76ac58081c80dbea269018b",
    "README.md": "42a61de40d0d75424a1ae649a6ec79ce0f05f3b246e1c15dc0167e829e5946fc",
    ".gitignore": "fb77eabed28643b6769baf95780a18611e0a34de0e9c72ff0a1af1673bfc4461",
}
EXPECTED_SEARCHES = {
    ("After-School Art Kit Order", "East Campus"),
    ("Science Lab Refill Order", "West Campus"),
}
EXPECTED_REPORT = (
    "| Name | ID | Date | Status |\n"
    "| --- | --- | --- | --- |\n"
    "| After-School Art Kit Order | com-186 | 2026-10-09 | awaiting-payment |\n"
    "| Science Lab Refill Order | com-586 | unknown | picking |\n"
    "\n"
    "Status comparison: After-School Art Kit Order is awaiting-payment; "
    "Science Lab Refill Order is picking, so the statuses differ.\n"
)
REFERENCE_MARKER_DIGEST = (
    "b5495d4b30b3deb714c93259c0a117fd03ff8b9cf2343d7f05090806f9725a71"
)
ALLOWED_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/order-audit.jsonl",
    ".harness/verify.py",
    ".orders/records.json",
    ".reference_solution",
    "README.md",
    "order-registry",
    "procurement_audit.md",
}
ALLOWED_DIRECTORIES = {
    ".harness",
    ".harness/runtime",
    ".orders",
}
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
            fail(f"protected order-registry input changed: {relative}")


def verify_workspace_shape() -> None:
    paths = []
    for top_level in ROOT.iterdir():
        if top_level.name == ".git":
            continue
        paths.append(top_level)
        if top_level.is_dir():
            paths.extend(top_level.rglob("*"))

    for path in paths:
        relative = path.relative_to(ROOT)
        name = relative.as_posix()
        allowed = name in (ALLOWED_DIRECTORIES if path.is_dir() else ALLOWED_FILES)
        if not allowed:
            fail(f"unexpected workspace artifact: {name}")

    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file()
        or hashlib.sha256(marker.read_bytes()).hexdigest()
        != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected workspace artifact: .reference_solution")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine order-registry execution trace was produced")
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
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden order-registry operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("all required order-registry operations must succeed")

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
        fail("both order branches must resolve to exactly one stable ID")
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
        fail("a complete order record was not retrieved")
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
        fail("procurement_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("procurement_audit.md is not the grounded two-row audit requested")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded audit and genuine Pi order-registry execution "
        "satisfy all constraints"
    )


if __name__ == "__main__":
    main()
