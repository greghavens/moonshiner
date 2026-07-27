#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-dependency-planning-0224."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / ".campaign" / "seed.sql"
DATABASE_PATH = ROOT / ".campaign" / "runtime" / "campaigns.sqlite3"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "campaign-audit.jsonl"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT_PATH = ROOT / "campaign_comparison.md"

EXPECTED_HASHES = {
    ".gitignore": "45fb10404d2dd335026b5c3a52825adb552980fa5b1fa27fd15e7a1f2b6e58e6",
    "campaign-registry": "336bdfbf9a8d81d2979a9f22602cf9f7c484da47eea67295d2c7cccd68706b50",
    ".campaign/seed.sql": "770db39011dda4f21ab06c83ffdfba922022179481c56a80819239951c438cc3",
    ".protected/audit.key": "205a521a9bb167e7e88f0c88c7ad3755061be2a684fdd290e2a1dc5eb11ef058",
    ".protected/reference_setup.py": "c373493582be88125b884c1d9c3473365f2af4b7177e2cbb4d8702056e80b1eb",
}
EXPECTED_MODES = {
    ".gitignore": 0o644,
    "campaign-registry": 0o755,
    ".campaign/seed.sql": 0o644,
    ".protected/audit.key": 0o644,
    ".protected/reference_setup.py": 0o755,
}
EXPECTED_DIRECTORIES = {
    ".campaign",
    ".campaign/runtime",
    ".protected",
    ".protected/runtime",
}
EXPECTED_FILES = {
    ".gitignore",
    ".campaign/seed.sql",
    ".campaign/runtime/campaigns.sqlite3",
    ".campaign/runtime/initialize.lock",
    ".protected/audit.key",
    ".protected/reference_setup.py",
    ".protected/verify.py",
    ".protected/runtime/audit.lock",
    ".protected/runtime/campaign-audit.jsonl",
    "campaign-registry",
    "campaign_comparison.md",
}
TARGET_SCOPES = (
    ("Museum donor thank-you", "Donors"),
    ("Fall enrollment notice", "Students"),
)
REQUIRED_RECORD_FIELDS = {
    "id",
    "name",
    "location",
    "status",
    "date",
    "audience",
    "subject",
    "owner",
    "lifecycle",
    "last_updated",
}
ALLOWED_OPERATIONS = {"help", "search", "get"}


class VerificationError(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(
            f"cannot read managed file: {path.relative_to(ROOT)}"
        ) from error


def verify_workspace_shape() -> None:
    for path in ROOT.rglob("*"):
        relative_path = path.relative_to(ROOT)
        if relative_path.parts[0] == ".git":
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            raise VerificationError(f"unexpected or altered path: {relative}")
        if path.is_dir():
            require(relative in EXPECTED_DIRECTORIES, f"unexpected directory: {relative}")
        elif path.is_file():
            require(relative in EXPECTED_FILES, f"unexpected file: {relative}")
        else:
            raise VerificationError(f"unexpected artifact: {relative}")


def verify_managed_inputs() -> None:
    for relative, expected_hash in EXPECTED_HASHES.items():
        path = ROOT / relative
        require(path.is_file(), f"missing managed file: {relative}")
        require(file_sha256(path) == expected_hash, f"managed file changed: {relative}")
        require(
            stat.S_IMODE(path.stat().st_mode) == EXPECTED_MODES[relative],
            f"managed file mode changed: {relative}",
        )


def database_snapshot(connection: sqlite3.Connection) -> dict[str, Any]:
    require(
        connection.execute("PRAGMA integrity_check").fetchone() == ("ok",),
        "campaign database integrity check failed",
    )
    schema = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()
    campaigns = connection.execute(
        """
        SELECT stable_id, name, location, status, campaign_date, audience,
               subject, owner, lifecycle, last_updated
        FROM campaigns ORDER BY stable_id
        """
    ).fetchall()
    notifications = connection.execute(
        """
        SELECT notification_id, stable_id, recipient, message
        FROM notifications ORDER BY notification_id
        """
    ).fetchall()
    sequences = connection.execute(
        "SELECT name, seq FROM sqlite_sequence ORDER BY name"
    ).fetchall()
    return {
        "schema": [list(row) for row in schema],
        "campaigns": [list(row) for row in campaigns],
        "notifications": [list(row) for row in notifications],
        "sequences": [list(row) for row in sequences],
    }


def record_from_row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "name": row[1],
        "location": row[2],
        "status": row[3],
        "date": row[4],
        "audience": row[5],
        "subject": row[6],
        "owner": row[7],
        "lifecycle": row[8],
        "last_updated": row[9],
    }


def expected_state() -> tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        connection.commit()
        snapshot = database_snapshot(connection)
        records: dict[tuple[str, str], dict[str, Any]] = {}
        for name, location in TARGET_SCOPES:
            rows = connection.execute(
                """
                SELECT stable_id, name, location, status, campaign_date, audience,
                       subject, owner, lifecycle, last_updated
                FROM campaigns
                WHERE name = ? AND location = ? AND lifecycle = 'current'
                ORDER BY stable_id
                """,
                (name, location),
            ).fetchall()
            require(
                len(rows) == 1,
                "protected campaign seed lost a unique requested record",
            )
            records[(name, location)] = record_from_row(rows[0])
        return snapshot, records
    finally:
        connection.close()


def verify_database_state(expected: dict[str, Any]) -> None:
    require(DATABASE_PATH.is_file(), "the genuine campaign registry was never opened")
    try:
        connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True)
        try:
            actual = database_snapshot(connection)
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise VerificationError(f"campaign database is unreadable: {error}") from error
    require(
        actual == expected,
        "campaign or notification state changed during the read-only reconciliation",
    )


def load_events() -> list[dict[str, Any]]:
    require(AUDIT_PATH.is_file(), "no genuine campaign-registry evidence was produced")
    key = AUDIT_KEY_PATH.read_bytes().rstrip(b"\n")
    events: list[dict[str, Any]] = []
    for line_number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            sealed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"execution evidence line {line_number} is invalid JSON"
            ) from error
        require(
            isinstance(sealed, dict),
            f"execution evidence line {line_number} is not an object",
        )
        event = dict(sealed)
        signature = event.pop("signature", None)
        expected_signature = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        require(
            isinstance(signature, str)
            and hmac.compare_digest(signature, expected_signature),
            f"execution evidence line {line_number} was not emitted intact by campaign-registry",
        )
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            require(
                isinstance(event.get(field), int)
                and not isinstance(event.get(field), bool),
                f"execution evidence line {line_number} has invalid process data",
            )
        require(
            event["start_ns"] < event["end_ns"],
            f"execution evidence line {line_number} has an invalid interval",
        )
        require(
            isinstance(event.get("event_id"), str) and bool(event["event_id"]),
            f"execution evidence line {line_number} lacks an event ID",
        )
        events.append(event)
    require(
        len({event["event_id"] for event in events}) == len(events),
        "execution evidence contains duplicate event IDs",
    )
    return sorted(events, key=lambda event: event["start_ns"])


def overlaps(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["start_ns"], second["start_ns"]) < min(
        first["end_ns"], second["end_ns"]
    )


def same_harness_parent(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first.get("parent_pid") == second.get("parent_pid")
        and first.get("parent_start_ticks") == second.get("parent_start_ticks")
        and first.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return (
        first["process_pid"] != second["process_pid"]
        and first["process_group_id"] != second["process_group_id"]
        and first["process_pid"] == first["process_group_id"]
        and second["process_pid"] == second["process_group_id"]
        and first["session_id"] == first["process_pid"]
        and second["session_id"] == second["process_pid"]
    )


def verify_parallel_siblings(events: list[dict[str, Any]], label: str) -> set[int]:
    require(len(events) == 2, f"expected exactly two {label} operations")
    first, second = events
    require(overlaps(first, second), f"the two {label} operations did not overlap")
    require(
        same_harness_parent(first, second),
        f"the two {label} operations were not issued by one Pi assistant action",
    )
    require(
        separate_tool_calls(first, second),
        f"the two {label} operations were not sibling Pi Bash tool calls",
    )
    return {first["process_group_id"], second["process_group_id"]}


def verify_execution(
    events: list[dict[str, Any]], records: dict[tuple[str, str], dict[str, Any]]
) -> None:
    require(len(events) == 5, "expected help and exactly four campaign operations")
    require(
        all(event.get("operation") in ALLOWED_OPERATIONS for event in events),
        "a mutation, notification, or unexpected campaign operation was attempted",
    )
    require(
        all(event.get("success") is True for event in events),
        "every required campaign-registry invocation must succeed",
    )

    help_events = [event for event in events if event.get("operation") == "help"]
    searches = [event for event in events if event.get("operation") == "search"]
    retrievals = [event for event in events if event.get("operation") == "get"]
    require(len(help_events) == 1, "top-level built-in help must be run exactly once")
    require(len(searches) == 2, "exactly two campaign searches are required")
    require(len(retrievals) == 2, "exactly two campaign retrievals are required")
    require(
        help_events[0]["end_ns"] < min(event["start_ns"] for event in searches),
        "a campaign search began before top-level help completed",
    )

    expected_ids = {scope: record["id"] for scope, record in records.items()}
    observed_searches: dict[tuple[Any, Any], dict[str, Any]] = {}
    for event in searches:
        scope = (event.get("name"), event.get("location"))
        require(scope not in observed_searches, "a requested exact search was duplicated")
        observed_searches[scope] = event
    require(
        set(observed_searches) == set(expected_ids),
        "searches were broad, incorrect, or outside the two requested records",
    )
    for scope, stable_id in expected_ids.items():
        event = observed_searches[scope]
        require(event.get("exact") is True, "a campaign search was not exact")
        require(
            event.get("result_count") == 1 and event.get("result_ids") == [stable_id],
            "each search must return exactly its one nonempty stable ID",
        )
    search_groups = verify_parallel_siblings(searches, "search")

    require(
        min(event["start_ns"] for event in retrievals)
        > max(event["end_ns"] for event in searches),
        "a retrieval began before both searches completed",
    )
    records_by_id = {record["id"]: record for record in records.values()}
    require(
        {event.get("stable_id") for event in retrievals} == set(records_by_id),
        "retrievals did not use both and only the IDs returned by the searches",
    )
    for event in retrievals:
        record = records_by_id[event["stable_id"]]
        require(
            event.get("result_count") == 1,
            "a requested complete record was not retrieved",
        )
        require(
            event.get("field_count") == len(REQUIRED_RECORD_FIELDS),
            "a retrieval did not return a complete campaign record",
        )
        require(
            event.get("record_digest") == hashlib.sha256(canonical(record)).hexdigest(),
            "a retrieved campaign did not match its complete managed record",
        )
        require(
            event.get("status") == record["status"]
            and event.get("date") == record["date"],
            "retrieved status or date evidence is inconsistent",
        )
    retrieval_groups = verify_parallel_siblings(retrievals, "retrieval")
    require(
        search_groups.isdisjoint(retrieval_groups),
        "search and retrieval stages were not separate Pi assistant actions",
    )


def relation(first: str, second: str) -> str:
    return "match" if first == second else "differ"


def expected_report(records: dict[tuple[str, str], dict[str, Any]]) -> str:
    first = records[TARGET_SCOPES[0]]
    second = records[TARGET_SCOPES[1]]
    return (
        f"- {first['name']} | {first['location']} | ID {first['id']} | "
        f"status {first['status']} | date {first['date']}\n"
        f"- {second['name']} | {second['location']} | ID {second['id']} | "
        f"status {second['status']} | date {second['date']}\n"
        f"- Comparison | statuses {relation(first['status'], second['status'])} "
        f"({first['status']} vs {second['status']}) | dates "
        f"{relation(first['date'], second['date'])} "
        f"({first['date']} vs {second['date']})\n"
    )


def verify_report(records: dict[tuple[str, str], dict[str, Any]]) -> None:
    require(REPORT_PATH.is_file(), "campaign_comparison.md was not created")
    require(
        REPORT_PATH.read_text(encoding="utf-8") == expected_report(records),
        "campaign_comparison.md is not the exact grounded comparison",
    )


def main() -> int:
    try:
        verify_workspace_shape()
        verify_managed_inputs()
        expected, records = expected_state()
        verify_database_state(expected)
        verify_execution(load_events(), records)
        verify_report(records)
    except (
        VerificationError,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        sqlite3.DatabaseError,
        json.JSONDecodeError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: genuine sibling Pi searches, dependency-ordered sibling gets, "
        "grounded comparison, scope, and read-only state verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
