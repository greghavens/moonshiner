#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "catalog-audit.jsonl"
DATABASE_PATH = ROOT / ".catalog" / "runtime" / "catalog.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-education-audit-0092-v1"

EXPECTED_HASHES = {
    "course-catalog": "a3908ee32439df8e79f30f476272e93d5ceba69f27fa185d96758515dcec15ec",
    ".catalog/seed.sql": "764ee0bbee0ca6b39484233bcfe277afd37ccb0be612e98b8f58ada156befa3a",
    "README.md": "43febb2ba6e574ea29d0ef735e808138184f2a2169e4f455e84854448f9e369b",
    ".gitignore": "0876c0f20aa5dda445aa3bc7ab13c3a246a08cd4721410a62dfa3ddea3348d64",
}
EXPECTED_DATABASE_DIGEST = "6fe23f73565676657a19c2bf8f2a1a2b8532d1cea870c59009feb7722870d7d1"
EXPECTED_SEARCHES = {
    ("Environmental Economics", "Downtown Campus"),
    ("Oral History Workshop", "North Campus"),
}
EXPECTED_REPORT = (
    "- Environmental Economics: open.\n"
    "- Oral History Workshop: waitlisted.\n"
    "- Result: The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_FILES = {
    ".catalog/runtime/catalog.sqlite3",
    ".catalog/runtime/catalog.sqlite3-shm",
    ".catalog/runtime/catalog.sqlite3-wal",
    ".catalog/runtime/initialize.lock",
    ".catalog/seed.sql",
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/catalog-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "course-catalog",
    "status_audit.md",
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
            fail(f"protected catalog input changed: {relative}")


def verify_workspace_files() -> None:
    observed = set()
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if relative == ".git" or relative.startswith(".git/"):
            continue
        if path.is_file() or path.is_symlink():
            observed.add(relative)
    unexpected = sorted(observed - ALLOWED_WORKSPACE_FILES)
    if unexpected:
        fail("unexpected workspace file: " + unexpected[0])


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite course catalog was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            courses = connection.execute(
                """
                SELECT stable_id, name, campus, status, lifecycle
                FROM courses ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"course catalog database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("course catalog database integrity check failed")
    snapshot = {
        "courses": [list(row) for row in courses],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("course catalog state changed during the read-only audit")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine course-catalog execution trace was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"audit event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the catalog")
        for field in ("start_ns", "end_ns", "process_pid", "process_group_id"):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        events.append(event)
    return sorted(events, key=lambda item: item["start_ns"])


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
        first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first.get("session_id") == first["process_pid"]
        and second.get("session_id") == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("a create, update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required course-catalog operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first catalog action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next catalog action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("campus")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-campus searches were not run")
    if any(event.get("result_count") != 1 for event in searches):
        fail("both lookups must resolve to exactly one current stable ID")
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
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in gets) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = {event["result_ids"][0] for event in searches}
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current course record was not retrieved")
    if (
        not overlaps(gets[0], gets[1])
        or not same_harness_parent(gets[0], gets[1])
        or not separate_tool_calls(gets[0], gets[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in gets
    }:
        fail("search and retrieval stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("status_audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("status_audit.md is not the exact grounded three-bullet audit")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_files()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded course status audit and genuine Pi catalog execution "
        "satisfy every dependency, concurrency, and read-only constraint"
    )


if __name__ == "__main__":
    main()
