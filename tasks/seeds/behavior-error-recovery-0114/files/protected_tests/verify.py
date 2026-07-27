#!/usr/bin/env python3
"""Protected deterministic verifier for the Pi error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "expense_processing_seed.sql"
DB_PATH = ROOT / ".expense-processing-runtime" / "ledger.sqlite3"
AUDIT_PATH = ROOT / ".expense-processing-runtime" / "audit.jsonl"
REPORT_PATH = ROOT / "processing_availability.txt"
AUDIT_KEY = b"moonshiner-pi-expense-recovery-0114-v1"
MINIMUM_OVERLAP_NS = 250_000_000

EXPECTED_HASHES = {
    ".gitignore": "b853fe1163e8c0a0004aebd32187fe5d6ef488b941930a797bf8bbd453f180bc",
    "README.md": "9008a4ef951c0f235fe676329c1cfff263f7ecf25b0e3c8aefa33c8393ee72e3",
    "bin/expense-processing": "df2751fd1620df0be7f56f1eb1c58d8112bfa5c4edb7c940192786c953100251",
    "data/expense_processing_seed.sql": "a59f5e4a9c39d935fcdab55793fd088247dd32f073076d5b33ffa384c11de4ef",
}
TARGETS = (
    ("Train fare 114", "Chicago", "2026-09-15"),
    ("Team lunch 114", "Boston", "2026-09-15"),
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        require(
            path.is_file() and sha256(path) == expected,
            f"protected input changed: {relative}",
        )


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def canonical_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
    return connection


def expected_windows(connection: sqlite3.Connection) -> dict[tuple[str, str, str], dict]:
    expected: dict[tuple[str, str, str], dict] = {}
    for expense_name, location, processing_date in TARGETS:
        row = connection.execute(
            "SELECT * FROM processing_windows "
            "WHERE expense_name = ? AND location = ? AND processing_date = ?",
            (expense_name, location, processing_date),
        ).fetchone()
        require(row is not None, "protected target processing window is missing")
        expected[(expense_name, location, processing_date)] = {
            "expense_name": row["expense_name"],
            "location": row["location"],
            "processing_date": row["processing_date"],
            "availability": row["availability"],
            "transient_failures": row["transient_failures"],
        }
    require(
        sorted(value["transient_failures"] for value in expected.values()) == [0, 1],
        "protected scenario must contain exactly one transient first-attempt failure",
    )
    return expected


def table_rows(connection: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    ]


def verify_state(
    canonical_db: sqlite3.Connection,
    expected: dict[tuple[str, str, str], dict],
) -> None:
    require(DB_PATH.is_file(), "the genuine expense-processing executable was not run")
    actual = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    actual.row_factory = sqlite3.Row
    try:
        for table in ("expenses", "processing_windows", "mutation_log", "notifications"):
            require(
                table_rows(actual, table) == table_rows(canonical_db, table),
                f"forbidden state change detected in {table}",
            )
        attempts = actual.execute(
            "SELECT expense_name, location, processing_date, attempt_count "
            "FROM attempts ORDER BY expense_name, location, processing_date"
        ).fetchall()
        wanted_attempts = sorted(
            (
                expense_name,
                location,
                processing_date,
                1 + int(window["transient_failures"]),
            )
            for (expense_name, location, processing_date), window in expected.items()
        )
        require(
            [tuple(row) for row in attempts] == wanted_attempts,
            "availability attempt state does not show one retained branch and one retry",
        )
    finally:
        actual.close()


def load_events() -> list[dict[str, Any]]:
    require(AUDIT_PATH.is_file(), "no genuine expense-processing evidence was produced")
    try:
        raw_lines = AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        require(len(raw_lines) == 3, "exactly three availability executions are required")
        events = [json.loads(line) for line in raw_lines]
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid execution evidence: {exc}") from exc

    for event in events:
        require(isinstance(event, dict), "each execution event must be an object")
        signature = event.pop("signature", None)
        wanted = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        require(
            isinstance(signature, str) and hmac.compare_digest(signature, wanted),
            "execution evidence was not emitted intact by the genuine client",
        )
        require(event.get("version") == 1, "unsupported execution evidence version")
        for field in (
            "start_ns",
            "end_ns",
            "process_pid",
            "process_group_id",
            "session_id",
            "parent_pid",
        ):
            require(isinstance(event.get(field), int), f"invalid evidence field {field}")
        require(event["start_ns"] < event["end_ns"], "invalid execution interval")
    return sorted(events, key=lambda event: event["start_ns"])


def overlap_ns(left: dict[str, Any], right: dict[str, Any]) -> int:
    return min(left["end_ns"], right["end_ns"]) - max(
        left["start_ns"], right["start_ns"]
    )


def same_harness_parent(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["parent_pid"] == right["parent_pid"]
        and left.get("parent_start_ticks") == right.get("parent_start_ticks")
        and left.get("parent_start_ticks") != "unavailable"
    )


def separate_tool_calls(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return (
        left["process_group_id"] != right["process_group_id"]
        and left["process_pid"] == left["process_group_id"]
        and right["process_pid"] == right["process_group_id"]
        and left["session_id"] == left["process_pid"]
        and right["session_id"] == right["process_pid"]
    )


def request_key(event: dict[str, Any]) -> tuple[str, str, str]:
    request = event.get("request")
    require(isinstance(request, dict), "execution evidence has no request identity")
    key = (
        request.get("expense_name"),
        request.get("location"),
        request.get("processing_date"),
    )
    require(all(isinstance(value, str) for value in key), "invalid request identity")
    return key


def expected_result(window: dict[str, Any]) -> dict[str, str]:
    return {
        "expense_name": window["expense_name"],
        "location": window["location"],
        "processing_date": window["processing_date"],
        "availability": window["availability"],
    }


def verify_execution(
    events: list[dict[str, Any]],
    expected: dict[tuple[str, str, str], dict],
) -> tuple[dict[str, str], dict[str, str]]:
    require(
        all(event.get("operation") == "availability" for event in events),
        "a forbidden expense-processing operation was executed",
    )
    initial = events[:2]
    retry = events[2]
    require(
        {request_key(event) for event in initial} == set(TARGETS),
        "the first action did not contain exactly the two requested checks",
    )
    require(
        overlap_ns(initial[0], initial[1]) >= MINIMUM_OVERLAP_NS,
        "the initial checks were not concurrent",
    )
    require(
        same_harness_parent(initial[0], initial[1])
        and separate_tool_calls(initial[0], initial[1]),
        "the initial checks were not sibling Pi Bash tool calls",
    )

    successes = [event for event in initial if event.get("success") is True]
    failures = [event for event in initial if event.get("success") is False]
    require(
        len(successes) == 1 and len(failures) == 1,
        "the initial partial failure was not retained branch-by-branch",
    )
    successful = successes[0]
    failed = failures[0]
    successful_key = request_key(successful)
    failed_key = request_key(failed)
    require(
        expected[successful_key]["transient_failures"] == 0
        and successful.get("result") == expected_result(expected[successful_key]),
        "the initially successful branch result is not grounded in its check",
    )
    require(
        expected[failed_key]["transient_failures"] == 1
        and failed.get("error_kind") == "transient"
        and isinstance(failed.get("error"), str),
        "the failed initial branch was not an explicit transient error",
    )

    require(
        retry["start_ns"] > max(event["end_ns"] for event in initial),
        "the retry began before both initial results were available",
    )
    require(
        request_key(retry) == failed_key,
        "the retry did not target only the transiently failed branch",
    )
    require(retry.get("success") is True, "the one allowed retry did not succeed")
    require(
        retry.get("result") == expected_result(expected[failed_key]),
        "the retry result is not grounded in the failed branch's processing window",
    )
    require(
        retry["process_group_id"]
        not in {event["process_group_id"] for event in initial},
        "the retry did not occur in a later Bash tool call",
    )
    require(
        same_harness_parent(initial[0], retry),
        "the initial and retry calls were not issued by the same Pi harness run",
    )
    return expected_result(expected[successful_key]), expected_result(expected[failed_key])


def verify_report(
    expected: dict[tuple[str, str, str], dict],
    retained: dict[str, str],
    retried: dict[str, str],
) -> None:
    require(REPORT_PATH.is_file(), "processing_availability.txt is missing")
    lines = [
        " | ".join(
            (
                expected[target]["expense_name"],
                expected[target]["location"],
                expected[target]["processing_date"],
                expected[target]["availability"],
            )
        )
        for target in TARGETS
    ]
    lines.append(
        f"Recovery: retained {retained['expense_name']}; "
        f"retried {retried['expense_name']} once."
    )
    lines.append("No expense retrieved or changed; no notice sent.")
    wanted = "\n".join(lines) + "\n"
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise VerificationError(f"cannot read deliverable: {exc}") from exc
    require(
        actual == wanted,
        "processing_availability.txt is not the exact grounded four-line report",
    )


def main() -> int:
    try:
        verify_protected_inputs()
        canonical_db = canonical_connection()
        try:
            expected = expected_windows(canonical_db)
            verify_state(canonical_db, expected)
            retained, retried = verify_execution(load_events(), expected)
            verify_report(expected, retained, retried)
        finally:
            canonical_db.close()
    except (
        VerificationError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        sqlite3.Error,
    ) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(
        "PASS: genuine parallel checks, retained success, one-branch retry, "
        "and negative constraints verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
