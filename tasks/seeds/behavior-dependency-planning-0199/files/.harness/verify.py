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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "application-audit.jsonl"
DATABASE_PATH = ROOT / ".applications_data" / "runtime" / "applications.sqlite3"
REPORT_PATH = ROOT / "public-services-audit.md"
AUDIT_KEY = b"moonshiner-pi-application-audit-0199-v1"

EXPECTED_HASHES = {
    "application-registry": "0b93c562f2628f5c076d59fa9020491362c4e7a448040947158ed354d91df275",
    ".harness/reference_setup.py": "bf18145a7016b55005a77960eb9f62e8bdb97eff2a66ddb4b3ff8966f044212d",
    ".applications_data/seed.sql": "e5e7f6ff09b79764db4af85a44b466bb99a090a12282128f63e5bd6385aef88a",
    "README.md": "ff3bd25c1a001737e6eb720c61c2d948003bcef4dc7995e8b4a6820fb903b519",
    ".gitignore": "b8251aa5230adc8b5db63991174e14c6d786362c511d509c9b64e725961a60d2",
}
EXPECTED_MODES = {
    "application-registry": 0o755,
    ".harness/reference_setup.py": 0o755,
    ".applications_data/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".applications_data",
    ".applications_data/runtime",
    ".harness",
    ".harness/runtime",
}
EXPECTED_FILES = {
    ".applications_data/runtime/initialize.lock",
    ".applications_data/runtime/applications.sqlite3",
    ".applications_data/seed.sql",
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/application-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "application-registry",
    "public-services-audit.md",
}
EXPECTED_SCHEMA_DIGEST = "3d4df0c8f0c0d666e5df7cf036f10fda6c899017198b361ccc063a23fd40962e"
EXPECTED_DATABASE_DIGEST = "84fccbc15b2dcc44b536e2861a40018df1e8724afbb8ccd8d54bfd5cb0ae473d"
EXPECTED_SEARCH_IDS = {
    ("Pet license renewal — Juniper", "Aurora"): "pub-299",
    ("Community hall permit — spring fair", "Lakewood"): "pub-699",
}
EXPECTED_REPORT = (
    "- Pet license renewal — Juniper | ID: pub-299 | location: Aurora | "
    "status: approved | date: 2026-08-27\n"
    "- Community hall permit — spring fair | ID: pub-699 | location: "
    "Lakewood | status: cancellation-requested | date: 2026-08-29\n"
    "- Comparison | status: different (approved vs cancellation-requested) | "
    "date: different (2026-08-27 vs 2026-08-29)\n"
    "- Recommendation | Community hall permit — spring fair (pub-699) is a "
    "cancellation candidate; recommend cancellation only; no change made; "
    "explicit confirmation required before cancellation\n"
)
EXPECTED_RECORDS = {
    "pub-299": {
        "name": "Pet license renewal — Juniper",
        "stable_id": "pub-299",
        "location": "Aurora",
        "status": "approved",
        "date": "2026-08-27",
        "applicant": "Juniper Patel",
        "application_type": "pet-license",
        "lifecycle": "current",
    },
    "pub-699": {
        "name": "Community hall permit — spring fair",
        "stable_id": "pub-699",
        "location": "Lakewood",
        "status": "cancellation-requested",
        "date": "2026-08-29",
        "applicant": "Spring Fair Committee",
        "application_type": "facility-permit",
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
            fail(f"protected application-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected application-registry input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite application registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            applications = connection.execute(
                """
                SELECT stable_id, name, location, status, application_date,
                       applicant, application_type, lifecycle
                FROM applications ORDER BY stable_id
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
        fail(f"application registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("application registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("application registry schema changed")
    snapshot = {
        "applications": [list(row) for row in applications],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "application registry state changed; requested, distractor, "
            "archived, and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine application-registry execution trace was produced")
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
            fail(
                f"audit event {number} was not emitted intact by "
                "application-registry"
            )
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
        fail("an update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required application-registry operation must succeed")

    searches, gets = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in gets] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("name"), event.get("location")): event for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact name-and-location searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both application lookups must resolve to one current stable ID")
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
        fail("a complete current application record was not retrieved")
    for event in gets:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail(
                "a retrieved application record was not returned completely "
                "and intact"
            )
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
        fail("public-services-audit.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail(
            "public-services-audit.md is not the exact grounded read-only "
            "comparison and confirmation-gated recommendation"
        )


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded application comparison and genuine Pi registry "
        "execution satisfy dependency, concurrency, read-only, and "
        "confirmation constraints"
    )


if __name__ == "__main__":
    main()
