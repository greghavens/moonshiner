#!/usr/bin/env python3
"""Protected deterministic verifier for the candidate dependency task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
SEED = ROOT / "candidate_seed.sql"
CACHE = ROOT / ".pytest_cache" / "candidate-desk"
DATABASE = CACHE / "candidates.sqlite3"
AUDIT_LOG = CACHE / "events.jsonl"
REPORT = ROOT / "recruiter-handoff.md"
AUDIT_KEY = b"candidate-desk-executable-audit-v1"
TARGETS = (
    ("Casey Evans - Regional Sales", "Sales"),
    ("Devon Flores - Research Analyst", "Research"),
)
STABLE_ID = re.compile(r"^rec-[0-9]+$")
FORBIDDEN = {
    "list", "preferences", "availability", "create", "update", "cancel", "notify",
}
TABLES = (
    "candidates", "saved_preferences", "availability", "notifications", "mutation_log",
)
MINIMUM_OVERLAP_NS = 100_000_000


class VerificationFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationFailure(message)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def signature(value: dict) -> str:
    return hmac.new(AUDIT_KEY, canonical(value), hashlib.sha256).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(SEED.read_text(encoding="utf-8"))
    connection.row_factory = sqlite3.Row
    return connection


def table_rows(connection: sqlite3.Connection, table: str) -> list[tuple]:
    return [tuple(row) for row in connection.execute(
        f"SELECT * FROM {table} ORDER BY 1"
    ).fetchall()]


def expected_records(connection: sqlite3.Connection) -> list[dict]:
    records: list[dict] = []
    for name, location in TARGETS:
        matches = connection.execute(
            "SELECT id FROM candidates WHERE name = ? AND location = ? ORDER BY id",
            (name, location),
        ).fetchall()
        require(len(matches) == 1, f"protected target is not unique: {name}")
        record = connection.execute(
            "SELECT id, name, location, status, interview_date, coordinator, notes "
            "FROM candidates WHERE id = ?",
            (matches[0]["id"],),
        ).fetchone()
        require(record is not None, f"protected full record is missing: {name}")
        records.append(dict(record))
    return records


def load_events() -> list[dict]:
    require(AUDIT_LOG.is_file(), "no genuine candidate executable audit was recorded")
    events: list[dict] = []
    for number, line in enumerate(AUDIT_LOG.read_bytes().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            supplied = event.pop("signature")
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise VerificationFailure(f"audit line {number} is invalid: {error}") from error
        require(
            hmac.compare_digest(str(supplied), signature(event)),
            f"audit line {number} was not produced intact by candidate_desk.py",
        )
        events.append(event)
    require(len(events) >= 4, "expected two searches and two full-record retrievals")
    return events


def overlap_ns(first: dict, second: dict) -> int:
    return min(first["ended_ns"], second["ended_ns"]) - max(
        first["started_ns"], second["started_ns"]
    )


def verify_execution(events: list[dict], records: list[dict]) -> None:
    for event in events:
        operation = event.get("operation")
        require(operation not in FORBIDDEN, f"forbidden operation attempted: {operation}")
        require(operation in {"search", "get"}, f"unexpected candidate operation: {operation}")
        require(event.get("ok") is True, f"candidate operation did not succeed: {operation}")
        require(
            isinstance(event.get("started_ns"), int)
            and isinstance(event.get("ended_ns"), int)
            and event["started_ns"] < event["ended_ns"],
            "candidate operation has invalid timing",
        )

    ordered = sorted(events, key=lambda event: event["started_ns"])
    search_by_target: dict[tuple[str, str], dict] = {}
    for target in TARGETS:
        matching = [
            event for event in ordered
            if event["operation"] == "search"
            and (
                event.get("arguments", {}).get("name"),
                event.get("arguments", {}).get("location"),
            ) == target
        ]
        require(matching, f"required exact search was not executed: {target[0]}")
        search_by_target[target] = matching[0]
    searches = list(search_by_target.values())
    require(
        ordered[0] in searches,
        "the first candidate operation was not one of the required searches",
    )
    require(
        searches[0].get("parent_pid") == searches[1].get("parent_pid"),
        "the searches were not launched from one shell execution",
    )
    require(
        searches[0].get("concurrency_batch")
        and searches[0].get("concurrency_batch") == searches[1].get("concurrency_batch"),
        "the searches did not enter one concurrent batch",
    )
    require(
        overlap_ns(searches[0], searches[1]) >= MINIMUM_OVERLAP_NS,
        "the two searches were not executed concurrently",
    )

    returned_ids: list[str] = []
    for target, record in zip(TARGETS, records):
        evidence = search_by_target[target].get("evidence") or {}
        require(evidence.get("match_count") == 1, f"search was not unique for {target[0]}")
        require(
            evidence.get("stable_ids") == [record["id"]]
            and STABLE_ID.fullmatch(record["id"]) is not None,
            f"search did not return exactly one stable ID for {target[0]}",
        )
        returned_ids.append(record["id"])

    searches_ended_ns = max(event["ended_ns"] for event in searches)
    require(
        all(
            event["operation"] != "get"
            or event["started_ns"] > searches_ended_ns
            for event in ordered
        ),
        "a retrieval began before both searches had returned",
    )
    after_searches = [
        event for event in ordered if event["started_ns"] > searches_ended_ns
    ]
    get_by_id: dict[str, dict] = {}
    for candidate_id in returned_ids:
        matching = [
            event for event in after_searches
            if event["operation"] == "get"
            and event.get("arguments", {}).get("candidate_id") == candidate_id
        ]
        require(matching, f"full record was not retrieved: {candidate_id}")
        get_by_id[candidate_id] = matching[0]
    gets = list(get_by_id.values())
    require(
        after_searches[0] in gets,
        "the next candidate operation was not one of the required retrievals",
    )
    require(
        gets[0].get("parent_pid") == gets[1].get("parent_pid"),
        "the retrievals were not launched from one shell execution",
    )
    require(
        gets[0].get("concurrency_batch")
        and gets[0].get("concurrency_batch") == gets[1].get("concurrency_batch"),
        "the retrievals did not enter one concurrent batch",
    )
    require(
        overlap_ns(gets[0], gets[1]) >= MINIMUM_OVERLAP_NS,
        "the two full-record retrievals were not executed concurrently",
    )
    for record in records:
        evidence = get_by_id[record["id"]].get("evidence") or {}
        expected_digest = hashlib.sha256(canonical(record)).hexdigest()
        require(
            evidence.get("stable_id") == record["id"]
            and evidence.get("record_digest") == expected_digest,
            f"full record was not retrieved intact for {record['name']}",
        )


def verify_read_only(canonical_db: sqlite3.Connection) -> None:
    require(DATABASE.is_file(), "candidate_desk.py did not initialize its data store")
    actual = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    try:
        for table in TABLES:
            require(
                table_rows(actual, table) == table_rows(canonical_db, table),
                f"read-only state changed in table {table}",
            )
    finally:
        actual.close()


def verify_report(records: list[dict]) -> None:
    require(REPORT.is_file(), "recruiter-handoff.md is missing")
    first, second = records
    expected = [
        f"- {first['name']} ({first['location']}) \u2014 stable ID {first['id']}; status: {first['status']}.",
        f"- {second['name']} ({second['location']}) \u2014 stable ID {second['id']}; status: {second['status']}.",
    ]
    if first["status"] == second["status"]:
        expected.append(f"- Status comparison: both candidates have status {first['status']}.")
    else:
        expected.append(
            f"- Status comparison: {first['status']} versus {second['status']}; "
            "the candidate statuses differ."
        )
    required = "\n".join(expected)
    actual = REPORT.read_text(encoding="utf-8")
    require(
        actual == required or actual == required + "\n",
        "recruiter-handoff.md does not match the exact grounded three-bullet format",
    )


def main() -> int:
    try:
        canonical_db = canonical_connection()
        try:
            records = expected_records(canonical_db)
            verify_report(records)
            events = load_events()
            verify_execution(events, records)
            verify_read_only(canonical_db)
        finally:
            canonical_db.close()
    except VerificationFailure as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: genuine parallel dependency flow, read-only state, and exact handoff verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
