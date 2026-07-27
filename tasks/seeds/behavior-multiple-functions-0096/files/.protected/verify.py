#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi function-selection task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / ".claims" / "registry.sqlite3"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "claims-audit.jsonl"
REFERENCE_OUTPUT_PATH = ROOT / ".protected" / "runtime" / "reference-output.json"
REPORT_PATH = ROOT / "active_claims.json"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
EXPECTED_HASHES = {
    "claims-registry": "16eb81db8cc3841b286e865d9da5bf5a7e8d53b1afe057b7b4df7569215622e8",
    ".claims/registry.sqlite3": "d99d71aba5435ff08bb5a1b2c79bd17827ec741a4a5d37489e8ddb766a2dcffe",
    ".claims/seed.sql": "3b6d7f14cb7e21b9de24d8ec5941e05089d1ef0220636573d5f49c9009f4b384",
    "README.md": "b046b2fb3aa2ec49be733954c7984eee139d3d0bb4bac6dcc262e043c89cc7c4",
    ".gitignore": "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e",
    ".protected/audit.key": "3fad161c73f19ac8973fdd011f52f7aa22744e95468fd4037af50c5a3ddadf0b",
}
BASELINE_FILES = {
    ".claims/registry.sqlite3",
    ".claims/seed.sql",
    ".gitignore",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    "README.md",
    "claims-registry",
}
ALLOWED_RUNTIME_FILES = {
    ".protected/runtime/claims-audit.jsonl",
    ".protected/runtime/reference-output.json",
}
ALLOWED_DELIVERABLES = {"active_claims.json", ".reference_solution"}


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
            fail(f"protected claims input changed: {relative}")


def expected_records() -> list[dict]:
    database = sqlite3.connect(
        f"{DATABASE_PATH.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            """
            SELECT id, claimant, type, location, status, filed_date
            FROM claims
            WHERE status = 'active' AND location = 'North Office'
            ORDER BY position
            """
        ).fetchall()
    finally:
        database.close()
    return [
        {
            "id": row["id"],
            "claimant": row["claimant"],
            "type": row["type"],
            "location": row["location"],
            "status": row["status"],
            "filed_date": row["filed_date"],
        }
        for row in rows
    ]


def expected_output(records: list[dict]) -> str:
    return json.dumps({"records": records}, sort_keys=True) + "\n"


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine claims-registry execution trace was produced")
    events: list[dict] = []
    key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
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
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return events


def verify_execution(events: list[dict], records: list[dict]) -> None:
    if len(events) != 1:
        fail("expected exactly one claims-registry data operation")
    event = events[0]
    if event.get("operation") != "list":
        fail("the structured list operation was not selected")
    if event.get("success") is not True:
        fail("the structured list operation did not succeed")
    if event.get("status") != "active":
        fail("the required structured status filter was not used")
    if event.get("location") != "North Office":
        fail("the required structured location filter was not used")
    expected_ids = [record["id"] for record in records]
    if event.get("result_count") != len(records):
        fail("the structured operation did not return the complete scoped set")
    if event.get("result_ids") != expected_ids:
        fail("the structured operation did not return only the scoped set")
    database_hash = EXPECTED_HASHES[".claims/registry.sqlite3"]
    if (
        event.get("database_before") != database_hash
        or event.get("database_after") != database_hash
        or file_sha256(DATABASE_PATH) != database_hash
    ):
        fail("the claims backing database changed")
    if not isinstance(event.get("process_pid"), int):
        fail("the genuine executable process evidence is incomplete")
    if event.get("process_start_ticks") == "unavailable":
        fail("the genuine executable process identity could not be verified")


def verify_report(records: list[dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("active_claims.json is missing")
    expected = expected_output(records)
    if REPORT_PATH.read_text(encoding="utf-8") != expected:
        fail("active_claims.json is not the exact grounded claims result")
    if (
        REFERENCE_OUTPUT_PATH.is_file()
        and REFERENCE_OUTPUT_PATH.read_text(encoding="utf-8") != expected
    ):
        fail("the reference execution output does not match the grounded result")


def verify_no_extra_files() -> None:
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(ROOT).parts
    }
    allowed = BASELINE_FILES | ALLOWED_RUNTIME_FILES | ALLOWED_DELIVERABLES
    extras = sorted(actual - allowed)
    if extras:
        fail("unexpected file created: " + extras[0])


def main() -> None:
    verify_protected_inputs()
    records = expected_records()
    if not records:
        fail("protected registry has no scoped active claim records")
    verify_execution(load_events(), records)
    verify_report(records)
    verify_no_extra_files()
    print(
        "PASS: genuine Pi execution used one structured, fully scoped, "
        "read-only claims operation and produced the exact grounded result"
    )


if __name__ == "__main__":
    main()
