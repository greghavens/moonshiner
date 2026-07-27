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
AUDIT_PATH = ROOT / ".harness" / "runtime" / "crm-audit.jsonl"
DATABASE_PATH = ROOT / ".crm" / "runtime" / "accounts.sqlite3"
OUTPUT_PATH = ROOT / "account-opened.txt"
AUDIT_KEY = b"moonshiner-pi-crm-format-0029-v1"

EXPECTED_HASHES = {
    "crm-account": "0d3669a236964d258ce15791bb2b24dbb0cf7224cfdbfb32d6cc4d6b19b9bc19",
    ".crm/seed.sql": "f530c947cbbb1590f7a2d089b72ebdce15e9a6338398846454fe5f1521b579f5",
    ".gitignore": "51effa93cc9f77a3d3619a0df97570b40fade8ecb68945248bf9acc6382f47d5",
}
EXPECTED_DATABASE_DIGEST = "508d3ae451ead65e435c5a356631074c20d30c8f7d80908d707b624083cfc1a4"
EXPECTED_RESULT_DIGEST = "85c6ceda5a6c94472e06a19bd66d0f4dc8ad98b2a534ec3f7a19b54d7cc68642"
EXPECTED_OUTPUT = "crm-129 | Arbor Foods 029 | West | active\n"
ALLOWED_WORKSPACE_ARTIFACTS = {
    ".crm",
    ".crm/runtime",
    ".crm/runtime/accounts.sqlite3",
    ".crm/runtime/initialize.lock",
    ".crm/seed.sql",
    ".gitignore",
    ".harness",
    ".harness/reference_setup.py",
    ".harness/runtime",
    ".harness/runtime/crm-audit.jsonl",
    ".harness/verify.py",
    "account-opened.txt",
    "crm-account",
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
            fail(f"protected CRM input changed: {relative}")


def verify_database_state() -> None:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite CRM was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            accounts = connection.execute(
                """
                SELECT stable_id, name, region, status, lifecycle
                FROM accounts
                ORDER BY stable_id
                """
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        fail(f"CRM database is unreadable: {error}")

    if integrity != ("ok",):
        fail("CRM database integrity check failed")
    snapshot = {"accounts": [list(row) for row in accounts]}
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("CRM state changed while opening the account")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine crm-account execution evidence was produced")
    events = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"CRM audit event {number} is not valid JSON")
        if not isinstance(signed, dict):
            fail(f"CRM audit event {number} is not an object")
        event = dict(signed)
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"CRM audit event {number} was not emitted intact by the executable")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "parent_pid",
            "process_group_id",
            "session_id",
        ):
            if not isinstance(event.get(field), int):
                fail(f"CRM audit event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"CRM audit event {number} has an invalid execution interval")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 1:
        fail("crm-account must be invoked exactly once")
    event = events[0]
    if event.get("operation") != "open":
        fail("the sole CRM operation was not an account open")
    if event.get("argument_count") != 1:
        fail("the CRM executable did not receive one stable ID operand")
    if event.get("stable_id") != "crm-129":
        fail("the requested account ID was not grounded correctly")
    if event.get("success") is not True or event.get("result_count") != 1:
        fail("the requested account was not opened successfully")
    if event.get("result_digest") != EXPECTED_RESULT_DIGEST:
        fail("the opened full account record was not returned intact")


def verify_output() -> None:
    if not OUTPUT_PATH.is_file():
        fail("the tool-managed account output is missing")
    if OUTPUT_PATH.read_text(encoding="utf-8") != EXPECTED_OUTPUT:
        fail("the tool-managed account output is not the requested exact record")


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
    verify_output()
    verify_no_extra_artifacts()
    print("PASS: exactly one genuine CRM open targeted crm-129")


if __name__ == "__main__":
    main()
