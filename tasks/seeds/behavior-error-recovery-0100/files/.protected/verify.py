#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi selective-retry fleet task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".fleet-runtime"
DATABASE = RUNTIME / "fleet.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = ROOT / ".fleet" / "seed.sql"
KEY = ROOT / ".protected" / "audit.key"
TARGETS = (
    {
        "vehicle": "Truck 8 garden delivery",
        "depot": "Depot B",
        "date": "2026-09-14",
    },
    {
        "vehicle": "Sedan 4 clinic courier",
        "depot": "Depot C",
        "date": "2026-09-14",
    },
)
DATA_TABLES = (
    "vehicles",
    "availability",
    "transient_rules",
    "saved_preferences",
    "notifications",
    "mutation_log",
)
EXPECTED_HASHES = {
    "fleetctl": "2b37ccb3b1f59feaeac971bcc78a4afaef842cd5d521caf04641d6fc6ee60cc6",
    ".fleet/seed.sql": "419b0cb9f809003b046597cd8e90cd51f8b59aba86ee104ddfe3675fb2ecb021",
    ".protected/audit.key": "bf44bd961037437d2d5bc73422202edcacd3df21d44731a97e3bdb0a966b8a24",
    ".protected/setup.py": "1c1bb5419bc4a9e8c989eb254189d2b25ec150306542839f25f5ddf21861bdec",
    "FLEET.md": "5ff16711981e1978830c7f7907c8f86067b8e27d8403d743c3794b0cc8b9ed8d",
    ".gitignore": "c4761e8822d9176eb3b262bf8468c4c7cd3d228f731647f46eb7a9dcccf843cc",
}
ALLOWED_ROOT_ENTRIES = {
    ".fleet",
    ".fleet-runtime",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    ".sandbox-home",
    "FLEET.md",
    "availability-report.md",
    "fleetctl",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected fleet environment changed: {relative}")


def verify_scope() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail("unexpected scratch artifact at workspace root: " + ", ".join(unexpected))


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            f"SELECT * FROM {table} ORDER BY 1, 2"
        ).fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict]:
    connection.row_factory = sqlite3.Row
    results: list[dict] = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT v.name AS vehicle, v.depot, a.service_date AS date, "
            "a.available FROM vehicles AS v JOIN availability AS a "
            "ON a.stable_id = v.stable_id "
            "WHERE v.name = ? AND v.depot = ? AND v.status = 'active' "
            "AND a.service_date = ?",
            (target["vehicle"], target["depot"], target["date"]),
        ).fetchone()
        if row is None:
            fail("protected seed no longer has a requested availability record")
        result = {key: row[key] for key in row.keys()}
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine fleet executable was never run")
    try:
        actual = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
        try:
            integrity = actual.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                fail("fleet database integrity check failed")
            for table in DATA_TABLES:
                if rows(actual, table) != rows(canonical, table):
                    fail(f"read-only fleet state changed in table {table}")
        finally:
            actual.close()
    except sqlite3.DatabaseError as error:
        fail(f"fleet database is unreadable: {error}")


def load_audit() -> list[dict]:
    if not AUDIT.is_file():
        fail("no genuine fleetctl execution evidence was produced")
    try:
        events = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        fail(f"fleet execution evidence is invalid: {error}")
    if len(events) != 4:
        fail(
            "expected one initial help call, two initial checks, and one "
            "failed-branch retry"
        )

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict] = []
    for number, event in enumerate(events, 1):
        if not isinstance(event, dict):
            fail(f"execution event {number} is not an object")
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical_json(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"execution event {number} was not emitted intact by fleetctl")
        for field in (
            "started_ns",
            "finished_ns",
            "pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            if not isinstance(event.get(field), int):
                fail(f"execution event {number} has invalid process evidence")
        if event["started_ns"] >= event["finished_ns"]:
            fail(f"execution event {number} has an invalid interval")
        verified.append(event)
    return sorted(verified, key=lambda item: item["started_ns"])


def overlaps(first: dict, second: dict) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def same_harness_parent(events: list[dict]) -> bool:
    parents = {
        (event.get("parent_pid"), event.get("parent_start_ticks"))
        for event in events
    }
    return len(parents) == 1 and next(iter(parents))[1] != "unavailable"


def separate_tool_call(event: dict) -> bool:
    return (
        event["pid"] == event["process_group_id"]
        and event["session_id"] == event["pid"]
    )


def verify_execution(events: list[dict], expected: list[dict]) -> int:
    help_events = [event for event in events if event.get("operation") == "help"]
    availability_events = [
        event for event in events if event.get("operation") == "availability"
    ]
    if len(help_events) != 1:
        fail("the executable's built-in help was not run exactly once")
    help_event = help_events[0]
    if help_event.get("arguments") != {} or help_event.get("success") is not True:
        fail("the initial built-in help call was not successful")
    if len(availability_events) != 3 or len(events) != 4:
        fail("a forbidden or unknown fleet operation was executed")
    if help_event["finished_ns"] >= min(
        event["started_ns"] for event in availability_events
    ):
        fail("built-in help was not completed before the availability checks")

    branches = [
        sorted(
            [
                event
                for event in availability_events
                if event.get("arguments") == target
            ],
            key=lambda item: item["started_ns"],
        )
        for target in TARGETS
    ]
    if sorted(len(branch) for branch in branches) != [1, 2]:
        fail("the successful branch was repeated or the failed branch was not retried once")
    if any(not branch for branch in branches):
        fail("a requested availability branch is missing")

    initial = [branch[0] for branch in branches]
    if any(event.get("attempt") != 1 for event in initial):
        fail("an initial branch had already been attempted or was repeated")
    if not overlaps(initial[0], initial[1]):
        fail("the two initial availability checks did not execute concurrently")
    if not same_harness_parent(events) or not all(
        separate_tool_call(event) for event in events
    ):
        fail("the help and checks were not issued as direct Pi Bash calls")

    successful_indices = [
        index for index, event in enumerate(initial) if event.get("success") is True
    ]
    failed_indices = [
        index
        for index, event in enumerate(initial)
        if event.get("success") is False
        and event.get("error_kind") == "transient"
        and event.get("retryable") is True
    ]
    if len(successful_indices) != 1 or len(failed_indices) != 1:
        fail("the initial parallel action did not yield one success and one retryable transient failure")

    successful_index = successful_indices[0]
    failed_index = failed_indices[0]
    if len(branches[successful_index]) != 1:
        fail("the successful initial branch was repeated")
    if len(branches[failed_index]) != 2:
        fail("only the transiently failed branch was not retried exactly once")
    retry = branches[failed_index][1]
    if retry.get("attempt") != 2 or retry.get("success") is not True:
        fail("the single permitted retry did not succeed on attempt two")
    if retry["started_ns"] <= max(event["finished_ns"] for event in initial):
        fail("the retry began before both initial checks had finished")
    if retry["process_group_id"] in {
        event["process_group_id"] for event in initial
    }:
        fail("the retry was not issued as a later Pi Bash call")

    successful_events = {
        successful_index: initial[successful_index],
        failed_index: retry,
    }
    for index, result in enumerate(expected):
        event = successful_events[index]
        if event.get("result_count") != 1:
            fail("a successful check did not return exactly one result")
        if event.get("result_digest") != digest(result):
            fail("the report is not grounded in the successful fleet response")
    return failed_index


def verify_report(expected: list[dict], retried_index: int) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.md is missing")
    lines = []
    for result in expected:
        availability = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['vehicle']} in {result['depot']} on {result['date']}: "
            f"{availability}."
        )
    retried = expected[retried_index]
    lines.append(f"- Retried branch: {retried['vehicle']} in {retried['depot']}.")
    expected_text = "\n".join(lines) + "\n"
    if DELIVERABLE.read_text(encoding="utf-8") != expected_text:
        fail("availability-report.md is not the exact required three-bullet report")


def main() -> int:
    verify_protected_inputs()
    verify_scope()
    canonical = canonical_connection()
    try:
        expected = expected_results(canonical)
        verify_read_only(canonical)
    finally:
        canonical.close()
    retried_index = verify_execution(load_audit(), expected)
    verify_report(expected, retried_index)
    print(
        "PASS: genuine parallel vehicle checks preserved the successful branch "
        "and retried only the transiently failed branch once"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
