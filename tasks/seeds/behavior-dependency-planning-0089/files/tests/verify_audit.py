#!/usr/bin/env python3
"""Protected deterministic verifier for the account audit behavior."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RECEIPT_DIR = ROOT / "audit_receipts"
RECEIPT_KEY = b"moonshiner-account-audit-v1"
TARGETS = [
    ("Indigo Travel Cooperative", "International"),
    ("Juniper Neighborhood Market", "Southwest Region"),
]


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def proof_for(value: dict) -> str:
    return hmac.new(RECEIPT_KEY, canonical(value).encode(), hashlib.sha256).hexdigest()


def receipt_name(operation: str, key: str) -> str:
    encoded = canonical({"key": key, "operation": operation}).encode()
    return f"{operation}-{hashlib.sha256(encoded).hexdigest()[:16]}.json"


def load_receipt(path: Path, failures: list[str]) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"invalid receipt {path.name}: {error}")
        return {}
    supplied = value.pop("proof", None)
    if not supplied or not hmac.compare_digest(str(supplied), proof_for(value)):
        failures.append(f"receipt {path.name} was not produced by the audit executable")
        return {}
    return value


def expected_records() -> list[dict]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript((ROOT / "data" / "accounts.sql").read_text())
    records = []
    for name, location in TARGETS:
        rows = connection.execute(
            "SELECT stable_id, name, location, status FROM accounts "
            "WHERE name = ? AND location = ? AND is_stale = 0 ORDER BY stable_id",
            (name, location),
        ).fetchall()
        if len(rows) != 1:
            raise AssertionError("protected account data must resolve each target exactly once")
        records.append(dict(rows[0]))
    return records


def verify_report(records: list[dict], failures: list[str]) -> None:
    report = ROOT / "audit_report.md"
    if not report.is_file():
        failures.append("audit_report.md is missing")
        return
    raw_lines = report.read_text().splitlines()
    if len(raw_lines) != 3 or any(not line.startswith("- ") for line in raw_lines):
        failures.append("audit_report.md must contain exactly three Markdown bullet lines and no other text")
        return
    folded = [line.casefold() for line in raw_lines]
    for index, record in enumerate(records):
        required = (record["name"], record["stable_id"], record["status"])
        if not all(str(value).casefold() in folded[index] for value in required):
            failures.append(f"record bullet {index + 1} is missing its name, stable ID, or retrieved status")
    status_a, status_b = records[0]["status"], records[1]["status"]
    if not all(value.casefold() in folded[2] for value in (status_a, status_b)):
        failures.append("the third bullet must compare both retrieved status values")
    relation_words = ("same", "both") if status_a == status_b else ("different", "differ")
    if not any(word in folded[2] for word in relation_words):
        failures.append("the status-comparison bullet does not state the correct relationship")


def main() -> int:
    failures: list[str] = []
    records = expected_records()
    expected_files = {"forbidden.json"}
    searches = []
    gets = []

    for (name, location), record in zip(TARGETS, records):
        search_path = RECEIPT_DIR / receipt_name("search", f"{name}\0{location}")
        get_path = RECEIPT_DIR / receipt_name("get", record["stable_id"])
        expected_files.update((search_path.name, get_path.name))
        searches.append(load_receipt(search_path, failures))
        gets.append(load_receipt(get_path, failures))

    actual_files = {path.name for path in RECEIPT_DIR.glob("*.json")}
    if actual_files != expected_files:
        failures.append("unexpected or missing audit receipt files")
    try:
        forbidden = json.loads((RECEIPT_DIR / "forbidden.json").read_text())
    except (OSError, json.JSONDecodeError):
        forbidden = {"invalid": True}
    if forbidden != {}:
        failures.append("a forbidden account operation was attempted")

    for (name, location), record, receipt in zip(TARGETS, records, searches):
        expected = {
            "exclude_stale": True,
            "location": location,
            "match_count": 1,
            "name": name,
            "operation": "search",
            "parallel_verified": True,
            "stable_ids": [record["stable_id"]],
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            failures.append(f"search receipt for {name} does not prove one current stable ID")

    search_actions = {receipt.get("action") for receipt in searches}
    get_actions = {receipt.get("action") for receipt in gets}
    if len(search_actions) != 1 or None in search_actions:
        failures.append("the searches were not issued as one concurrent action")
    if len(get_actions) != 1 or None in get_actions:
        failures.append("the gets were not issued as one concurrent action")
    if search_actions == get_actions:
        failures.append("search and get must be separate consecutive account actions")

    predecessor = next(iter(search_actions), None)
    for record, receipt in zip(records, gets):
        expected = {
            "operation": "get",
            "parallel_verified": True,
            "predecessor_action": predecessor,
            "record": record,
            "stable_id": record["stable_id"],
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            failures.append(f"get receipt for {record['name']} violates the dependency plan")

    verify_report(records, failures)
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: concurrent read-only searches, dependent gets, and exact report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
