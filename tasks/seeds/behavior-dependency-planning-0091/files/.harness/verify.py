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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "directory-audit.jsonl"
DATABASE_PATH = ROOT / ".directory" / "runtime" / "directory.sqlite3"
REPORT_PATH = ROOT / "status_audit.md"
AUDIT_KEY = b"moonshiner-pi-staff-audit-0091-v1"

EXPECTED_HASHES = {
    "staff-directory": "6f69077c41fa3296d44af92c12004ff23e2aca06786e4a3ec7f53a6517135ad3",
    ".directory/seed.sql": "919bd155dc7b629d952bcbb63d5f8e8ae599f398becc4e8ed0ad3449b2af17b5",
    "README.md": "25f121eb542232ffe965112fe82f9db0f152aa086b739d4af99bd5ca46216541",
    ".gitignore": "0c612cbb3116e407f0f5963e359ab8283fdd7b4243725c06519d46cf8932105a",
}
EXPECTED_DATABASE_DIGEST = "3dc74712d221fb50767614903e2344acc78794865533d2fa5d9da72709b89670"
EXPECTED_SEARCHES = {
    ("Priya Nair", "Analytics"),
    ("Mateo Silva", "Customer Success"),
}
EXPECTED_REPORT = (
    "Priya Nair in Analytics has status Active.\n"
    "Mateo Silva in Customer Success has status On leave.\n"
    "The returned statuses differ.\n"
)
ALLOWED_OPERATIONS = {"search", "get"}
ALLOWED_WORKSPACE_ARTIFACTS = {
    ".directory",
    ".directory/runtime",
    ".directory/runtime/directory.sqlite3",
    ".directory/runtime/initialize.lock",
    ".directory/seed.sql",
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/directory-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "staff-directory",
    "status_audit.md",
}
IGNORED_HARNESS_METADATA = {".agents", ".codex", ".git"}


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
            fail(f"protected directory input changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite staff directory was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            employees = connection.execute(
                """
                SELECT stable_id, name, department, status, lifecycle
                FROM employees ORDER BY stable_id
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
        fail(f"staff directory database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("staff directory database integrity check failed")
    snapshot = {
        "employees": [list(row) for row in employees],
        "notifications": [list(row) for row in notifications],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("staff directory state changed during the read-only audit")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine staff-directory execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by the directory")
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
        fail("every required staff-directory operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first directory action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next directory action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("department")) for event in searches
    }
    if observed_searches != EXPECTED_SEARCHES:
        fail("the two required exact name-and-department searches were not run")
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
        fail("a complete current staff record was not retrieved")
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
        fail("status_audit.md is not the exact grounded three-line audit")


def verify_no_extra_artifacts() -> None:
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in IGNORED_HARNESS_METADATA:
            continue
        if relative.as_posix() not in ALLOWED_WORKSPACE_ARTIFACTS:
            fail(f"unexpected sandbox artifact remains: {relative.as_posix()}")


def main() -> None:
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    verify_no_extra_artifacts()
    print(
        "PASS: grounded staff status audit and genuine Pi directory execution "
        "satisfy every dependency and read-only constraint"
    )


if __name__ == "__main__":
    main()
