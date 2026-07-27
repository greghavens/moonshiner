#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi format-sensitivity seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "library-audit.jsonl"
DATABASE_PATH = ROOT / ".library" / "runtime" / "catalog.sqlite3"
DELIVERABLE_PATH = ROOT / "invocation.txt"
AUDIT_KEY = b"moonshiner-pi-library-audit-0013-v1"

EXPECTED_HASHES = {
    "README.md": "cc7d2250fbac74d250e2b6777dcc8acadc087c808fd34f74419055241eedbe1d",
    "libraryctl": "8adf1d17015db637bfbbaf9a37066fb1ebbe1a2224be8e5fee4610721c8de2f7",
    ".library/seed.sql": "f7f912d5e10ec35cb0355948017c32ab2406e6c43b65734462754851053cad94",
    ".gitignore": "a4a1c96d386e454d329353efe4b0667b6a4ae020cc7e00cf70a96bc619fec379",
}
EXPECTED_DATABASE_DIGEST = "988a282f96ab4b0aec4d9157c3fa7163fac317b2580984c79737183f3edde448"
EXPECTED_RECORD_DIGEST = "da8e5942c1d2b9d562b99484ac5cbc651c9cd8458877341a35a9c5102359534e"
EXPECTED_DELIVERABLE = b"library_get(id=\"lib-113\")\n"
ALLOWED_WORKSPACE_ENTRIES = {
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/library-audit.jsonl",
    ".harness/verify.py",
    ".library",
    ".library/runtime",
    ".library/runtime/catalog.sqlite3",
    ".library/runtime/initialize.lock",
    ".library/seed.sql",
    "README.md",
    "invocation.txt",
    "libraryctl",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected library input changed: {relative}")


def verify_scope() -> None:
    unexpected = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts[0] == ".git":
            continue
        if path.is_symlink() or relative.as_posix() not in ALLOWED_WORKSPACE_ENTRIES:
            unexpected.append(relative.as_posix())
    if unexpected:
        fail("unexpected scratch artifact in workspace: " + ", ".join(sorted(unexpected)))


def verify_database() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite library registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT stable_id, title, creator, publication_year, format,
                       location, status, edition
                FROM title_records ORDER BY stable_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"library registry database is unreadable: {exc}")
    if integrity != ("ok",):
        fail("library registry database integrity check failed")
    digest = hashlib.sha256(canonical([list(row) for row in rows])).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("library registry state changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine libraryctl execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by libraryctl")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) < 2:
        fail(
            "libraryctl must begin with a help request and execute exactly "
            "one registry read"
        )
    help_events = [event for event in events if event.get("operation") == "help"]
    read_events = [event for event in events if event.get("operation") == "get"]
    if len(read_events) != 1:
        fail("the library registry read operation must execute exactly once")
    if len(help_events) + len(read_events) != len(events):
        fail("the execution trace contains an unsupported library operation")
    for help_event in help_events:
        if (
            help_event.get("arguments") != ["--help"]
            or help_event.get("success") is not True
        ):
            fail("a ./libraryctl --help request was not completed successfully")
    help_event = events[0]
    if help_event.get("operation") != "help":
        fail("the required initial ./libraryctl --help request was not completed")
    event = read_events[0]
    if event.get("operation") != "get":
        fail("the one registry operation was not the required complete-record read")
    if event.get("stable_id") != "lib-113":
        fail("the registry read did not use the requested stable ID")
    if not event.get("success") or not event.get("found"):
        fail("the requested complete title record was not retrieved successfully")
    if event.get("record_sha256") != EXPECTED_RECORD_DIGEST:
        fail("the execution trace does not identify the complete target record")
    for operation_event in events:
        operation = operation_event["operation"]
        for field in (
            "started_ns",
            "finished_ns",
            "process_pid",
            "process_group_id",
            "parent_pid",
        ):
            if not isinstance(operation_event.get(field), int):
                fail(
                    f"the {operation} execution trace has invalid "
                    f"process evidence: {field}"
                )
        if operation_event["started_ns"] >= operation_event["finished_ns"]:
            fail(f"the {operation} execution trace has an invalid time interval")
        for field in ("process_start_ticks", "parent_start_ticks"):
            if not isinstance(operation_event.get(field), str):
                fail(
                    f"the {operation} execution trace lacks "
                    f"process evidence: {field}"
                )
    if help_event["finished_ns"] > event["started_ns"]:
        fail("the library registry read began before the help request completed")


def verify_deliverable() -> None:
    if not DELIVERABLE_PATH.is_file():
        fail("invocation.txt is missing")
    if DELIVERABLE_PATH.read_bytes() != EXPECTED_DELIVERABLE:
        fail("invocation.txt does not satisfy the exact one-line Python-call format")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database()
    verify_execution(load_events())
    verify_deliverable()
    print(
        "PASS: genuine complete-record retrieval and exact grounded "
        "Python-call rendering verified"
    )


if __name__ == "__main__":
    main()
