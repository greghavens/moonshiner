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
AUDIT_PATH = ROOT / ".protected" / "runtime" / "campaign-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
DATABASE_PATH = ROOT / ".campaigns" / "runtime" / "campaigns.sqlite3"
REPORT_PATH = ROOT / "handoff.md"

EXPECTED_HASHES = {
    "campaign-registry": "7a84611d4fb41a9dc0f984d2884e08e4ae7d4e8f01743b8ef670f4c63daa5ffa",
    ".protected/audit.key": "2df539217a8d0876183ee84cf3d2a357d19c230ffc02bc45779159e88f592f5d",
    ".protected/reference_setup.py": "b0b1f35bbd5f9a066f9da13cd61f8e5f107e088b290031bd542fece5cd02a4c1",
    ".campaigns/seed.sql": "16bc4e1fddef9ef01bbffe65543c093ab90d9b63cd068261d42f6e1992a56f7d",
    "README.md": "8a05719fc7fd9afd0cb7d544672702a449dc7e13725164af1d0fa6f042a22d10",
    ".gitignore": "be1e11fa9e30f13340d8b7a4e74c9eeac102bf2c79c50dfd9c622958c2dd1612",
}
EXPECTED_MODES = {
    "campaign-registry": 0o755,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
    ".campaigns/seed.sql": 0o644,
    "README.md": 0o644,
    ".gitignore": 0o644,
}
EXPECTED_DIRECTORIES = {
    ".campaigns",
    ".campaigns/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".campaigns/runtime/initialize.lock",
    ".campaigns/runtime/campaigns.sqlite3",
    ".campaigns/seed.sql",
    ".gitignore",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/runtime/campaign-audit.jsonl",
    ".protected/verify.py",
    "README.md",
    "campaign-registry",
    "handoff.md",
}
EXPECTED_SCHEMA_DIGEST = "52ea08e2e5d53abc46c2d04dcec58e0679473a687b8ee11627ad2223bee3ae19"
EXPECTED_DATABASE_DIGEST = "fb00e4efc7842dfcc1d7cba6122f1cad793cffd0692575dbcb132301ceb1ba22"
EXPECTED_SEARCH_IDS = {
    ("Volunteer renewal reminder", "Volunteers"): "cmp-2047",
    ("North region service bulletin", "North Region"): "cmp-7812",
}
EXPECTED_REPORT = (
    "- Volunteer renewal reminder | ID: cmp-2047 | collection: Volunteers | "
    "status: scheduled | date: 2026-08-12\n"
    "- North region service bulletin | ID: cmp-7812 | collection: North "
    "Region | status: sent | date: 2026-07-18\n"
    "- Comparison | status: different (scheduled vs sent) | date: different "
    "(2026-08-12 vs 2026-07-18)\n"
)
EXPECTED_RECORDS = {
    "cmp-2047": {
        "title": "Volunteer renewal reminder",
        "stable_id": "cmp-2047",
        "collection": "Volunteers",
        "status": "scheduled",
        "date": "2026-08-12",
        "subject": "Please renew your volunteer registration",
        "audience": "Active volunteers with expiring registrations",
        "channel": "email",
        "owner": "Ari Moreno",
        "lifecycle": "current",
    },
    "cmp-7812": {
        "title": "North region service bulletin",
        "stable_id": "cmp-7812",
        "collection": "North Region",
        "status": "sent",
        "date": "2026-07-18",
        "subject": "Scheduled service changes for the North region",
        "audience": "North region subscribers",
        "channel": "email",
        "owner": "Morgan Lee",
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
            fail(f"protected campaign-registry input changed: {relative}")
        if stat.S_IMODE(path.stat().st_mode) != EXPECTED_MODES[relative]:
            fail(f"protected campaign-registry input mode changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite campaign registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            campaigns = connection.execute(
                """
                SELECT stable_id, title, collection, status, campaign_date,
                       subject, audience, channel, owner, lifecycle
                FROM campaigns ORDER BY stable_id
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
        fail(f"campaign registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("campaign registry database integrity check failed")
    schema_digest = hashlib.sha256(
        canonical({"schema": [list(row) for row in schema]})
    ).hexdigest()
    if schema_digest != EXPECTED_SCHEMA_DIGEST:
        fail("campaign registry schema changed")
    snapshot = {
        "campaigns": [list(row) for row in campaigns],
        "notifications": [list(row) for row in notifications],
        "sqlite_sequence": [list(row) for row in sqlite_sequence],
    }
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail(
            "campaign registry state changed; requested, distractor, archived, "
            "and notification records must remain untouched"
        )


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine campaign-registry execution trace was produced")
    audit_key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
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
        expected = hmac.new(audit_key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(
                f"audit event {number} was not emitted intact by "
                "campaign-registry"
            )
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"audit event {number} has an invalid execution interval")
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            fail(f"audit event {number} has no event ID")
        events.append(event)
    if len({event["event_id"] for event in events}) != len(events):
        fail("the campaign execution trace contains duplicate event IDs")
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
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_execution(events: list[dict]) -> None:
    if any(event.get("operation") not in ALLOWED_OPERATIONS for event in events):
        fail("an update, cancel, notify, or unknown operation was executed")
    if len(events) != 4:
        fail("expected exactly two searches followed by exactly two retrievals")
    if any(not event.get("success") for event in events):
        fail("every required campaign-registry operation must succeed")

    searches, retrievals = events[:2], events[2:]
    if [event.get("operation") for event in searches] != ["search", "search"]:
        fail("the first registry action must contain only both searches")
    if [event.get("operation") for event in retrievals] != ["get", "get"]:
        fail("the next registry action must contain only both retrievals")

    observed_searches = {
        (event.get("title"), event.get("collection")): event
        for event in searches
    }
    if set(observed_searches) != set(EXPECTED_SEARCH_IDS):
        fail("the two required exact title-and-collection searches were not run")
    for lookup, expected_id in EXPECTED_SEARCH_IDS.items():
        event = observed_searches[lookup]
        if event.get("result_count") != 1:
            fail("both campaign lookups must resolve to one current stable ID")
        if event.get("result_ids") != [expected_id]:
            fail("a search did not return its one protected current stable ID")
    if (
        not overlaps(searches[0], searches[1])
        or not same_harness_parent(searches[0], searches[1])
        or not separate_tool_calls(searches[0], searches[1])
    ):
        fail("the searches were not concurrent sibling Pi Bash tool calls")

    if min(event["start_ns"] for event in retrievals) <= max(
        event["end_ns"] for event in searches
    ):
        fail("a retrieval began before both search results were available")
    returned_ids = set(EXPECTED_SEARCH_IDS.values())
    if {event.get("stable_id") for event in retrievals} != returned_ids:
        fail("retrievals did not use exactly the IDs returned by the searches")
    if any(event.get("result_count") != 1 for event in retrievals):
        fail("a complete current campaign record was not retrieved")
    for event in retrievals:
        record = EXPECTED_RECORDS[event["stable_id"]]
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        if event.get("record_digest") != expected_digest:
            fail("a retrieved campaign record was not returned completely intact")
    if (
        not overlaps(retrievals[0], retrievals[1])
        or not same_harness_parent(retrievals[0], retrievals[1])
        or not separate_tool_calls(retrievals[0], retrievals[1])
    ):
        fail("the retrievals were not concurrent sibling Pi Bash tool calls")
    if {event["process_group_id"] for event in searches} & {
        event["process_group_id"] for event in retrievals
    }:
        fail("search and retrieval stages reused a Bash tool call")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("handoff.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("handoff.md is not the exact grounded three-bullet comparison")


def main() -> None:
    verify_workspace_shape()
    verify_protected_inputs()
    verify_database_state()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded campaign comparison and genuine Pi registry execution "
        "satisfy dependency, concurrency, read-only, and exact-format constraints"
    )


if __name__ == "__main__":
    main()
