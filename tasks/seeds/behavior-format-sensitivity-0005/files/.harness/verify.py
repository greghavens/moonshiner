#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi typed-argument lookup seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "support-audit.jsonl"
DATABASE_PATH = ROOT / ".support" / "runtime" / "support.sqlite3"
REFERENCE_MARKER = ROOT / ".reference_solution"
AUDIT_KEY = b"moonshiner-pi-support-audit-format-0005-v1"
REFERENCE_MARKER_DIGEST = (
    "ff39e503ca18da8af1555c63b597e33b8d90cb03cbc015457ce7e19a62bca3d2"
)

EXPECTED_HASHES = {
    "support-case": "8bb9c5f98c4f15f48089087c45bc6367f75fb7a030252885bd44d028edd50d04",
    ".support/seed.sql": "956b2018d20b259dac715660fc1c47e1d392f3af41177347d7584b424d485177",
    "README.md": "7bfff8c7bd67e8f81d90e3807fd6e0f05f9b3e074efb795c5d4110103997f577",
    ".gitignore": "dbc7fc2415a946d4a2233f4f0c5825c3778efcc08789976804e111009aafd49a",
}
EXPECTED_DATABASE_DIGEST = "0914e42f3eaed32335ef12de740e0d6b1ffac74a36dbe567639684561f505ecc"
EXPECTED_RESULT_FIELDS = [
    "description",
    "id",
    "lifecycle",
    "location",
    "name",
    "opened_on",
    "priority",
    "status",
]
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".harness",
    ".reference_solution",
    ".support",
    "README.md",
    "support-case",
}
ALLOWED_SUPPORT_STATIC = {"seed.sql"}
ALLOWED_SUPPORT_RUNTIME = {"initialize.lock", "support.sqlite3"}
ALLOWED_HARNESS_STATIC = {"reference_setup.py", "verify.py"}
ALLOWED_HARNESS_RUNTIME = {"support-audit.jsonl"}


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
            fail(f"protected support-registry input changed: {relative}")


def unexpected_names(directory: Path, allowed: set[str]) -> list[str]:
    if not directory.is_dir():
        fail(f"required protected directory is missing: {directory.relative_to(ROOT)}")
    return sorted(path.name for path in directory.iterdir() if path.name not in allowed)


def verify_scope() -> None:
    unexpected_root = unexpected_names(ROOT, ALLOWED_ROOT_ENTRIES)
    if unexpected_root:
        fail("unexpected workspace artifact: " + ", ".join(unexpected_root))
    if (
        REFERENCE_MARKER.is_file()
        and file_sha256(REFERENCE_MARKER) != REFERENCE_MARKER_DIGEST
    ):
        fail("unexpected reference marker content")

    support_allowed = ALLOWED_SUPPORT_STATIC | {"runtime"}
    unexpected_support = unexpected_names(ROOT / ".support", support_allowed)
    if unexpected_support:
        fail("unexpected .support artifact: " + ", ".join(unexpected_support))
    unexpected_support_runtime = unexpected_names(
        ROOT / ".support" / "runtime", ALLOWED_SUPPORT_RUNTIME
    )
    if unexpected_support_runtime:
        fail(
            "unexpected support runtime artifact: "
            + ", ".join(unexpected_support_runtime)
        )

    harness_allowed = ALLOWED_HARNESS_STATIC | {"runtime"}
    unexpected_harness = unexpected_names(ROOT / ".harness", harness_allowed)
    if unexpected_harness:
        fail("unexpected .harness artifact: " + ", ".join(unexpected_harness))
    unexpected_harness_runtime = unexpected_names(
        ROOT / ".harness" / "runtime", ALLOWED_HARNESS_RUNTIME
    )
    if unexpected_harness_runtime:
        fail(
            "unexpected harness runtime artifact: "
            + ", ".join(unexpected_harness_runtime)
        )


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite support registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            cases = connection.execute(
                """
                SELECT stable_id, name, location, status, opened_on, priority,
                       description, lifecycle
                FROM cases ORDER BY stable_id
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
        fail(f"support registry database is unreadable: {exc}")

    if integrity != ("ok",):
        fail("support registry database integrity check failed")
    snapshot = {
        "cases": [list(row) for row in cases],
        "notifications": [list(row) for row in notifications],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("support registry state changed during this lookup-only request")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine support-case execution evidence was produced")
    events: list[dict] = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        event = dict(signed_event)
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by support-case")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 1:
        fail("exactly one support-case execution is required")
    event = events[0]
    if event.get("operation") != "get":
        fail("the sole support-case execution was not the full-record get operation")
    if event.get("success") is not True:
        fail("the required support-case get operation did not succeed")
    if event.get("stable_id") != "sup-105":
        fail("the requested stable ID was not passed unchanged")
    if event.get("argument_type") != "str":
        fail("the stable ID was not handled as a string")
    if event.get("result_count") != 1 or event.get("result_id") != "sup-105":
        fail("the complete requested case was not retrieved")
    if event.get("result_fields") != EXPECTED_RESULT_FIELDS:
        fail("the get operation did not return the full case")
    if event.get("reference_marker") is not REFERENCE_MARKER.is_file():
        fail("support-case evidence does not belong to the current solution state")

    for field in (
        "start_ns",
        "end_ns",
        "process_pid",
        "process_group_id",
        "session_id",
        "parent_pid",
    ):
        if not isinstance(event.get(field), int):
            fail(f"genuine process evidence is missing field: {field}")
    if event["start_ns"] >= event["end_ns"]:
        fail("support-case process evidence has an invalid execution interval")
    if (
        not isinstance(event.get("process_start_ticks"), str)
        or event.get("process_start_ticks") == "unavailable"
        or not isinstance(event.get("parent_start_ticks"), str)
        or event.get("parent_start_ticks") == "unavailable"
    ):
        fail("support-case process identity evidence is incomplete")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database_state()
    verify_execution(load_events())
    print(
        "PASS: one genuine Pi support lookup retrieved the full case with the "
        "literal string ID and left all state unchanged"
    )


if __name__ == "__main__":
    main()
