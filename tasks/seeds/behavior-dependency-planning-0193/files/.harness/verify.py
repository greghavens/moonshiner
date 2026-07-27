#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi dependency-planning seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "catalog-audit.jsonl"
DATABASE_PATH = ROOT / ".library" / "runtime" / "catalog.sqlite3"
REPORT_PATH = ROOT / "library_check.md"
AUDIT_KEY = b"moonshiner-pi-library-audit-0193-v1"

EXPECTED_HASHES = {
    "library-catalog": "8437410defa540b4fcc2db98b0bfa2f5900a7a7fecdb9174cc838761e864a3c9",
    ".harness/reference_setup.py": "097510b037c266d5522d6b098cdf7072c06b7350006f4f12c525a24764a659cb",
    ".library/seed.sql": "37ca92092e2eebfb7de418224a28ab15e2789e5e345c7f237e335835eeea974c",
    "README.md": "2517eda74bd52919da180e6bff45ed1be12165418909e95c72f632c59fa7a544",
    ".gitignore": "27d426fd2417bbd970ffa26a04a04a5340e5b45720b9583081372bd0a8179c3e",
}
EXPECTED_MODES = {
    "library-catalog": 0o755,
    ".harness/reference_setup.py": 0o755,
    ".library/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".harness",
    ".harness/runtime",
    ".library",
    ".library/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/catalog-audit.jsonl",
    ".harness/verify.py",
    ".library/runtime/catalog.sqlite3",
    ".library/runtime/initialize.lock",
    ".library/seed.sql",
    "README.md",
    "library-catalog",
    "library_check.md",
}
EXPECTED_SCHEMA_DIGEST = "9eee419129b232caa1fa14d99bb47969efa8dc9c82e1f6b9b5ec0a0969043aa1"
EXPECTED_DATABASE_DIGEST = "8c41349e2352face92bd323c4f7dfb7ab5d3c7a74a072bca9c32d126dc473656"
EXPECTED_SEARCH_IDS = {
    ("River Almanac, fourth edition", "Central Branch"): "lib-293",
    ("Quiet Geometry workbook", "East Branch"): "lib-693",
}
EXPECTED_REPORT = (
    "River Almanac, fourth edition in Central Branch (lib-293) has status "
    "available and date 2026-08-09.\n"
    "Quiet Geometry workbook in East Branch (lib-693) has status checked-out "
    "and date 2026-08-10.\n"
    "Comparison: statuses differ (available versus checked-out), and dates "
    "differ (2026-08-09 versus 2026-08-10).\n"
)
EXPECTED_RECORDS = {
    "lib-293": {
        "title": "River Almanac, fourth edition",
        "stable_id": "lib-293",
        "branch": "Central Branch",
        "status": "available",
        "date": "2026-08-09",
        "format": "print",
        "collection": "Circulating",
        "lifecycle": "current",
    },
    "lib-693": {
        "title": "Quiet Geometry workbook",
        "stable_id": "lib-693",
        "branch": "East Branch",
        "status": "checked-out",
        "date": "2026-08-10",
        "format": "spiral-bound",
        "collection": "Course support",
        "lifecycle": "current",
    },
}
ALLOWED_OPERATIONS = {"search", "get"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            fail(f"unexpected scratch artifact or altered path: {relative}")
        if path.is_dir():
            if relative not in EXPECTED_DIRECTORIES:
                fail(f"unexpected scratch directory remains: {relative}")
        elif path.is_file():
            if relative not in EXPECTED_FILES:
                fail(f"unexpected scratch file remains: {relative}")
        else:
            fail(f"unexpected scratch artifact remains: {relative}")


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected catalog input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected catalog input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite library catalog was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            titles = connection.execute(
                """
                SELECT stable_id, title, branch, status, record_date,
                       format, collection, lifecycle
                FROM titles ORDER BY stable_id
                """
            ).fetchall()
            notifications = connection.execute(
                """
                SELECT notification_id, stable_id, message
                FROM notifications ORDER BY notification_id
                """
            ).fetchall()
            schema = connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_schema ORDER BY type, name
                """
            ).fetchall()
            sqlite_sequence = connection.execute(
                "SELECT name, seq FROM sqlite_sequence ORDER BY name"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"library catalog database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("library catalog database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("library catalog schema changed")
    snapshot = {
        "titles": [list(row) for row in titles],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "library catalog state changed; requested, distractor, archived, "
            "and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine library-catalog execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by library-catalog")
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
        fail("every required library-catalog operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first catalog action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next catalog action must contain only both retrievals")

    observed_searches = {
        (event.get("title"), event.get("branch")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact title-and-branch searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both title lookups must resolve to exactly one current stable ID")
        if event.get("result_ids") != [expected_id]:
            fail("a search did not return its one protected current stable ID")
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
    returned_ids = set(EXPECTED_SEARCH_IDS.values())
    if {event.get("stable_id") for event in gets} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in gets):
        fail("a complete current library record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved title record was not returned completely and intact")
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
        fail("library_check.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("library_check.md is not the exact grounded read-only comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded library comparison and genuine Pi catalog execution "
        "satisfy dependency, concurrency, and read-only constraints"
    )


if __name__ == "__main__":
    main()
