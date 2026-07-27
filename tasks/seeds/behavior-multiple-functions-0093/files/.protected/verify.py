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
DATABASE_PATH = ROOT / ".catalog" / "registry.sqlite3"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "catalog-audit.jsonl"
REPORT_PATH = ROOT / "active_titles.md"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
EXPECTED_HASHES = {
    "catalog-registry": "8fa1e16561451ae1aa7f73ad501c4170d52e3b5608f9f1232eb36a45684de701",
    ".catalog/registry.sqlite3": "55927a4bc283d2e8f8c928efc5f8896bab181cee7db4a19a2c1a0849581958e8",
    ".catalog/seed.sql": "1882dfb9b5d16330d2b81e420f6a157c7f8a0d1ebedbf8561ae54fc4cdab02db",
    "README.md": "bebbaf95a66a96217977852abd0a044f680f2d784a4e842eebde33633bf09fba",
    ".gitignore": "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e",
    ".protected/audit.key": "fe32fc0227810c59035713de90bbc90082aec308051ed463f61000dec3307f68",
}
BASELINE_FILES = {
    ".catalog/registry.sqlite3",
    ".catalog/seed.sql",
    ".gitignore",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    "README.md",
    "catalog-registry",
}
ALLOWED_RUNTIME_FILES = {
    ".protected/runtime/catalog-audit.jsonl",
    ".protected/runtime/reference-output.json",
}
ALLOWED_DELIVERABLES = {"active_titles.md", ".reference_solution"}


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


def expected_records() -> list[dict]:
    database = sqlite3.connect(
        f"{DATABASE_PATH.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            """
            SELECT id, name, location, status, date
            FROM titles
            WHERE status = 'active' AND location = 'East Branch'
            ORDER BY name COLLATE NOCASE, id
            """
        ).fetchall()
    finally:
        database.close()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "location": row["location"],
            "status": row["status"],
            "date": row["date"],
        }
        for row in rows
    ]


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine catalog-registry execution trace was produced")
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
        fail("expected exactly one catalog-registry operation")
    event = events[0]
    if event.get("operation") != "list":
        fail("the structured list operation was not selected")
    if event.get("success") is not True:
        fail("the structured list operation did not succeed")
    if event.get("status") != "active":
        fail("the required structured status filter was not used")
    if event.get("location") != "East Branch":
        fail("the required structured location filter was not used")
    if event.get("sort") != "name":
        fail("the registry's name ordering was not used")
    expected_ids = [record["id"] for record in records]
    if event.get("result_count") != len(records):
        fail("the structured operation did not return the complete scoped set")
    if event.get("result_ids") != expected_ids:
        fail("the structured operation did not return only the ordered scoped set")
    database_hash = EXPECTED_HASHES[".catalog/registry.sqlite3"]
    if (
        event.get("database_before") != database_hash
        or event.get("database_after") != database_hash
        or file_sha256(DATABASE_PATH) != database_hash
    ):
        fail("the catalog backing database changed")
    if not isinstance(event.get("process_pid"), int):
        fail("the genuine executable process evidence is incomplete")
    if event.get("process_start_ticks") == "unavailable":
        fail("the genuine executable process identity could not be verified")


def report_text(records: list[dict]) -> str:
    lines = [
        "| ID | Name | Location | Status | Date |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines.extend(
        "| {id} | {name} | {location} | {status} | {date} |".format(**record)
        for record in records
    )
    return "\n".join(lines) + "\n"


def verify_report(records: list[dict]) -> None:
    if not REPORT_PATH.is_file():
        fail("active_titles.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != report_text(records):
        fail("active_titles.md is not the exact ordered, grounded catalog result")


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
        fail("protected catalog has no scoped active title records")
    verify_execution(load_events(), records)
    verify_report(records)
    verify_no_extra_files()
    print(
        "PASS: genuine Pi execution used one structured, scoped, name-ordered "
        "read-only catalog operation and produced the exact grounded report"
    )


if __name__ == "__main__":
    main()
