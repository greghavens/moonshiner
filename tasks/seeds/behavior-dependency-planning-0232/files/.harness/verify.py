#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi course handoff task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "course-audit.jsonl"
REPORT_PATH = ROOT / "handoff.md"
KEY_PATH = ROOT / ".harness" / "audit.key"

EXPECTED_HASHES = {
    "course-registry": "7ee9b836d40de61cf515a6f717b53a48d2ef33f894d5e9ec9968aae920374200",
    ".catalog/courses.sqlite3": "fbef6384d41ff9698f977d4446032b4c0b78529356c21cbaf1f68c6e9f269738",
    ".harness/audit.key": "feb9d3ac6c1287335686cf994b1d7fc20b32c009fbcaac346cbc304e66f7c3f9",
    ".gitignore": "8a8970a4234b021d8c24c680d5eceb0c7defbcba00136be46fc3388b40f4875f",
}
EXPECTED_SEARCHES = {
    ("Museum collections care", "Arts Campus"): "course-ac-417",
    ("Community budgeting workshop", "Civic Campus"): "course-cc-284",
}
EXPECTED_RECORDS = {
    "course-ac-417": {
        "name": "Museum collections care",
        "campus": "Arts Campus",
        "status": "confirmed",
        "date": "2026-09-18",
    },
    "course-cc-284": {
        "name": "Community budgeting workshop",
        "campus": "Civic Campus",
        "status": "registration-open",
        "date": "2026-10-02",
    },
}
EXPECTED_REPORT = (
    "- Museum collections care | Arts Campus | course-ac-417 | "
    "status confirmed | date 2026-09-18\n"
    "- Community budgeting workshop | Civic Campus | course-cc-284 | "
    "status registration-open | date 2026-10-02\n"
    "- Comparison | statuses differ | dates differ\n"
    "- Changes | none\n"
)
FORBIDDEN_OPERATIONS = {"list", "update", "cancel", "notify"}


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
            fail(f"supplied registry input changed: {relative}")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine course-registry execution trace was produced")
    events = []
    key = KEY_PATH.read_bytes().strip()
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by course-registry")
        events.append(event)
    return sorted(events, key=lambda event: event.get("start_ns", -1))


def overlaps(first: dict, second: dict) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict, second: dict) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
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


def verify_search_stage(searches: list[dict]) -> set[str]:
    observed = {
        (event.get("name"), event.get("campus")): event for event in searches
    }
    if set(observed) != set(EXPECTED_SEARCHES):
        fail("the two exact name-and-campus searches were not executed")

    returned_ids: set[str] = set()
    for lookup, stable_id in EXPECTED_SEARCHES.items():
        event = observed[lookup]
        if event.get("result_count") != 1:
            fail(f"search did not resolve uniquely: {lookup[0]} at {lookup[1]}")
        if event.get("result_ids") != [stable_id]:
            fail(f"search did not return one auditable stable ID: {lookup[0]}")
        returned_ids.add(stable_id)
    return returned_ids


def verify_get_stage(gets: list[dict], returned_ids: set[str]) -> None:
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("gets did not use exactly the IDs returned by the corresponding searches")
    for event in gets:
        stable_id = event["stable_id"]
        expected = EXPECTED_RECORDS[stable_id]
        if event.get("result_count") != 1:
            fail(f"complete course record was not retrieved: {stable_id}")
        observed = {
            "name": event.get("returned_name"),
            "campus": event.get("returned_campus"),
            "status": event.get("returned_status"),
            "date": event.get("returned_date"),
        }
        if observed != expected:
            fail(f"retrieved record did not match its search branch: {stable_id}")


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") in FORBIDDEN_OPERATIONS for event in events):
        fail("a forbidden course-registry operation was executed")
    if len(events) != 5:
        fail("expected help, exactly two searches, and exactly two gets")
    if any(not event.get("success") for event in events):
        fail("help and all four required course-registry operations must succeed")

    help_event = events[0]
    if help_event.get("operation") != "help" or help_event.get("arguments") != [
        "--help"
    ]:
        fail("the registry was not first invoked with exactly --help")

    searches, gets = events[1:3], events[3:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first course-data action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next course-data action must contain only both gets")
    if min(event["start_ns"] for event in searches) <= help_event["end_ns"]:
        fail("a course search began before the --help invocation completed")

    returned_ids = verify_search_stage(searches)
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("searches were not two concurrent sibling Pi Bash-tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a get began before both search results were available")
    verify_get_stage(gets, returned_ids)
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("gets were not two concurrent sibling Pi Bash-tool calls")

    search_groups = {event["process_group_id"] for event in searches}
    get_groups = {event["process_group_id"] for event in gets}
    if search_groups & get_groups:
        fail("search and get stages reused a Bash-tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("handoff.md is not grounded in both complete course records")


def main() -> None:
    verify_protected_inputs()
    verify_execution(load_events())
    verify_report()
    print("PASS: genuine staged Pi execution produced the read-only course handoff")


if __name__ == "__main__":
    main()
