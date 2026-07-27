#!/usr/bin/env python3
"""Deterministic protected verifier for the grounded expense lookup."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "expense-audit.jsonl"
DATABASE_PATH = ROOT / ".expense-store" / "runtime" / "ledger.sqlite3"
FINDING_PATH = ROOT / "finding.txt"
AUDIT_KEY = b"moonshiner-pi-expense-audit-long-context-0014-v1"
EXPECTED_HASHES = {
    "expensectl": "49c085e552debaed78323649ccc41f4d1a166221e0bd82f59c357cb1dacd6054",
    ".expense-store/seed.sql": "49ecb6f06d0c8d1cdfe90008fb7b1545e8a8af270a03ed58b4c04d62edf8dee9",
    "README.md": "f8006ba0b5b3bd328122c420cf1bfcc36692f8c0310d7fba35ed522246f26c44",
    ".gitignore": "e62dabe04e4de46b68cc478b71f91685ad67ef9325f1d1febf9ba641093a9534",
}
EXPECTED_DATABASE_DIGEST = "9200fe167c9c576b600c790bad7e0d7101eac2463e7f11f211e0dc56b889fe1c"
EXPECTED_NAME = "Intercity Rail Fare"
EXPECTED_LOCATION = "Chicago"


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
            fail(f"protected expense environment changed: {relative}")


def load_database_snapshot() -> tuple[dict, list]:
    if not DATABASE_PATH.is_file():
        fail("the genuine SQLite expense ledger was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            rows = connection.execute(
                """
                SELECT stable_id, name, location, status, amount_cents,
                       incurred_on, lifecycle
                FROM expenses ORDER BY stable_id
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
        fail(f"expense ledger database is unreadable: {exc}")
    if integrity != ("ok",):
        fail("expense ledger database integrity check failed")

    snapshot = {
        "expenses": [list(row) for row in rows],
        "notifications": [list(row) for row in notifications],
    }
    if hashlib.sha256(canonical(snapshot)).hexdigest() != EXPECTED_DATABASE_DIGEST:
        fail("expense ledger state changed during the read-only lookup")

    if len(rows) < 40:
        fail("the protected long expense fixture lost its distractor records")
    targets = [
        row
        for row in rows
        if row[1] == EXPECTED_NAME
        and row[2] == EXPECTED_LOCATION
        and row[6] == "current"
    ]
    if len(targets) != 1:
        fail("the protected target is not uniquely resolvable")
    if (
        sum(
            row[1] == EXPECTED_NAME
            and row[2] != EXPECTED_LOCATION
            and row[6] == "current"
            for row in rows
        )
        < 2
    ):
        fail("same-name other-location distractors are missing")
    if (
        sum(
            row[1] != EXPECTED_NAME
            and EXPECTED_NAME in row[1]
            and row[2] == EXPECTED_LOCATION
            and row[6] == "current"
            for row in rows
        )
        < 4
    ):
        fail("slightly different Chicago distractors are missing")
    return snapshot, list(targets[0])


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine expensectl execution evidence was produced")
    events: list[dict] = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"execution event {number} is not valid JSON")
        if not isinstance(event, dict):
            fail(f"execution event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"execution event {number} was not emitted intact by expensectl")
        if event.get("client_version") != "expensectl-v1":
            fail(f"execution event {number} did not come from the supplied client")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
        ):
            if not isinstance(event.get(field), int):
                fail(f"execution event {number} has invalid process evidence")
        if event["start_ns"] >= event["end_ns"]:
            fail(f"execution event {number} has an invalid time interval")
        events.append(event)
    return sorted(events, key=lambda event: event["start_ns"])


def verify_execution(events: list[dict], target: list) -> None:
    if len(events) != 2:
        fail("expected exactly one scoped search and one dependent retrieval")
    search, get = events
    if search.get("operation") != "search" or get.get("operation") != "get":
        fail("expense operations were not exactly search followed by get")
    if not search.get("success") or not get.get("success"):
        fail("both required expense operations must succeed")
    if (
        search.get("name") != EXPECTED_NAME
        or search.get("location") != EXPECTED_LOCATION
    ):
        fail("the search was not scoped by the exact requested name and location")
    result_ids = search.get("result_ids")
    if (
        search.get("result_count") != 1
        or not isinstance(result_ids, list)
        or len(result_ids) != 1
        or not isinstance(result_ids[0], str)
        or not result_ids[0]
    ):
        fail("the scoped search did not return one auditable stable ID")
    if get.get("stable_id") != result_ids[0]:
        fail("get did not use exactly the stable ID returned by search")
    if get.get("stable_id") != target[0] or get.get("result_count") != 1:
        fail("get did not retrieve the unique complete current target")

    target_record = {
        "name": target[1],
        "stable_id": target[0],
        "location": target[2],
        "status": target[3],
        "amount_cents": target[4],
        "incurred_on": target[5],
        "lifecycle": target[6],
    }
    expected_record_digest = hashlib.sha256(canonical(target_record)).hexdigest()
    if get.get("record_digest") != expected_record_digest:
        fail("get evidence does not identify the complete target record")
    if get["start_ns"] <= search["end_ns"]:
        fail("retrieval began before the scoped search result was available")
    if get["process_group_id"] == search["process_group_id"]:
        fail("search and retrieval were combined in one Bash execution")


def verify_finding(target: list) -> None:
    if target[3] != "active":
        fail("the unique complete target no longer has required status active")
    expected = (
        f"Found: {target[1]} in {target[2]} has status {target[3]} "
        f"(stable ID {target[0]}).\n"
    )
    if not FINDING_PATH.is_file():
        fail("finding.txt is missing")
    if FINDING_PATH.read_text(encoding="utf-8") != expected:
        fail("finding.txt is not the exact grounded one-sentence result")


def main() -> None:
    verify_protected_inputs()
    _, target = load_database_snapshot()
    verify_execution(load_events(), target)
    verify_finding(target)
    print(
        "PASS: genuine scoped search, dependent ID retrieval, distractor "
        "resistance, grounded finding, and read-only state verified"
    )


if __name__ == "__main__":
    main()
