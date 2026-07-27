#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi XML-wrapper seed."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expenses-audit.jsonl"
DATABASE_PATH = ROOT / ".expenses" / "runtime" / "expenses.sqlite3"
DELIVERABLE_PATH = ROOT / "envelope.xml"
AUDIT_KEY = b"moonshiner-pi-expenses-audit-0014-v1"

EXPECTED_HASHES = {
    "README.md": "d43aa49249b94f2595f0116f3ebdc01c8c959cdb9a80e9b4584a1f66f93749d5",
    "expensesctl": "6eb82b7abc6de1a7c42191829956a52ade713a037835a3a739d5cf7866d95d41",
    ".expenses/seed.sql": "5b8e3802d26816ff91af4d42240ad5d0aedd220e6adba4484669e62cd8ffb740",
    ".gitignore": "1469411bc2ebef6588a324387ad0e87c77980b4004d6e668fbdbca8501ffc561",
}
EXPECTED_DATABASE_DIGEST = "ff16b68123b9b10669ed87e205dd77717df0a7248663dcc60d824b58171681da"
EXPECTED_RECORD_DIGEST = "0b1a69e18535655e7bbe0d1db5fb8fd2b520256fd39de383f2cc9d901161f71d"
EXPECTED_DELIVERABLE = b'<tool_call>expenses_get({"id":"exp-114"})</tool_call>'
ALLOWED_WORKSPACE_ENTRIES = {
    ".expenses",
    ".expenses/runtime",
    ".expenses/runtime/expenses.sqlite3",
    ".expenses/runtime/initialize.lock",
    ".expenses/seed.sql",
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/expenses-audit.jsonl",
    ".harness/verify.py",
    "README.md",
    "envelope.xml",
    "expensesctl",
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
            fail(f"protected expense input changed: {relative}")


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
        fail("the genuine SQLite expense registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT stable_id, name, location, status
                FROM expense_records ORDER BY stable_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        fail(f"expense registry database is unreadable: {exc}")
    if integrity != ("ok",):
        fail("expense registry database integrity check failed")
    digest = hashlib.sha256(canonical([list(row) for row in rows])).hexdigest()
    if digest != EXPECTED_DATABASE_DIGEST:
        fail("expense registry state changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expensesctl execution trace was produced")
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
            fail(f"audit event {number} was not emitted intact by expensesctl")
        events.append(event)
    return events


def verify_process_evidence(event: dict, label: str) -> None:
    for field in (
        "started_ns",
        "finished_ns",
        "process_pid",
        "process_group_id",
        "parent_pid",
    ):
        if not isinstance(event.get(field), int):
            fail(f"{label} execution trace has invalid process evidence: {field}")
    if event["started_ns"] >= event["finished_ns"]:
        fail(f"{label} execution trace has an invalid time interval")
    for field in ("process_start_ticks", "parent_start_ticks"):
        if not isinstance(event.get(field), str):
            fail(f"{label} execution trace lacks process evidence: {field}")


def verify_execution(events: list[dict]) -> None:
    if len(events) != 2:
        fail("expensesctl must execute one help request and one registry read")
    help_event, get_event = events

    if help_event.get("operation") != "help" or help_event.get("success") is not True:
        fail("the first executable request was not successful interface discovery")
    verify_process_evidence(help_event, "help")

    if get_event.get("operation") != "get":
        fail("the one registry operation was not the required read")
    if get_event.get("stable_id") != "exp-114":
        fail("the registry read did not use the requested stable ID")
    if get_event.get("mode") != "read-only":
        fail("the registry read was not executed in read-only mode")
    if not get_event.get("success") or not get_event.get("found"):
        fail("the requested complete expense record was not retrieved successfully")
    if get_event.get("record_sha256") != EXPECTED_RECORD_DIGEST:
        fail("the execution trace does not identify the complete target record")
    verify_process_evidence(get_event, "get")


def verify_deliverable() -> None:
    if not DELIVERABLE_PATH.is_file():
        fail("envelope.xml is missing")
    if DELIVERABLE_PATH.read_bytes() != EXPECTED_DELIVERABLE:
        fail("envelope.xml does not satisfy the exact one-wrapper format")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    verify_database()
    verify_execution(load_events())
    verify_deliverable()
    print(
        "PASS: genuine read-only expense retrieval and exact grounded "
        "XML wrapper verified"
    )


if __name__ == "__main__":
    main()
