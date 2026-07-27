#!/usr/bin/env python3
"""Deterministic protected verifier for the uncertain-cancellation audit."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / ".library" / "runtime.sqlite3"
SEED = ROOT / ".library" / "seed.sql"
REPORT = ROOT / "cancellation_report.md"
REASON = "record superseded by a corrected request"
EXPECTED_REPORT = (
    "Record lib-153 final status: cancelled.\n"
    "Settling verification made a retry necessary: no.\n"
)


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 1


def rows(connection: sqlite3.Connection, query: str) -> list[tuple]:
    return [tuple(row) for row in connection.execute(query).fetchall()]


def baseline_records() -> list[tuple]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED.read_text(encoding="utf-8"))
        return rows(
            connection,
            "SELECT id, title, status, cancellation_reason, updated_at "
            "FROM records ORDER BY id",
        )
    finally:
        connection.close()


def main() -> int:
    if not DATABASE.is_file():
        return fail("the library registry was not operated")
    if not REPORT.is_file():
        return fail("cancellation_report.md is missing")
    if REPORT.read_text(encoding="utf-8") != EXPECTED_REPORT:
        return fail("cancellation_report.md does not match the settled outcome")

    expected = baseline_records()
    connection = sqlite3.connect(DATABASE)
    try:
        actual = rows(
            connection,
            "SELECT id, title, status, cancellation_reason, updated_at "
            "FROM records ORDER BY id",
        )
        if len(actual) != len(expected):
            return fail("the record collection changed")

        expected_by_id = {row[0]: row for row in expected}
        actual_by_id = {row[0]: row for row in actual}
        for record_id, before in expected_by_id.items():
            after = actual_by_id.get(record_id)
            if after is None:
                return fail(f"record {record_id} is missing")
            if record_id != "lib-153" and after != before:
                return fail(f"unrelated record {record_id} changed")

        target = actual_by_id["lib-153"]
        if target[1] != expected_by_id["lib-153"][1]:
            return fail("the target title changed")
        if target[2] != "cancelled" or target[3] != REASON:
            return fail("lib-153 is not cancelled with the exact audit reason")
        if target[4] != "2032-04-12T16:00:00Z":
            return fail("the target was changed outside the registry operation")

        fault = connection.execute(
            "SELECT remaining FROM faults "
            "WHERE name = 'lib-153:cancel-timeout-after-commit'"
        ).fetchone()
        if fault is None or fault[0] != 0:
            return fail("the uncertain-commit path was not exercised exactly once")

        invocations = rows(
            connection,
            "SELECT sequence, operation, record_id, reason "
            "FROM invocations ORDER BY sequence",
        )
        if not invocations or invocations[0] != (1, "help", None, None):
            return fail("the first libraryctl invocation was not --help")
        operational_invocations = [
            (operation, record_id, reason)
            for _, operation, record_id, reason in invocations
            if operation != "help"
        ]
        expected_operational_invocations = [
            ("cancel", "lib-153", REASON),
            ("get", "lib-153", None),
        ]
        if operational_invocations != expected_operational_invocations:
            return fail(
                "after consulting help, libraryctl must be used only for the "
                "initial cancellation and settling fetch"
            )

        operations = rows(
            connection,
            "SELECT sequence, operation, record_id, reason, observed_status, "
            "resulting_status, outcome FROM operations ORDER BY sequence",
        )
        expected_operations = [
            (1, "cancel", "lib-153", REASON, "active", "cancelled", "committed_timeout"),
            (2, "get", "lib-153", None, "cancelled", "cancelled", "returned"),
        ]
        if operations != expected_operations:
            return fail(
                "operation history must show one uncertain cancellation followed "
                "by one settling fetch and no retry"
            )
    except sqlite3.Error as error:
        return fail(f"registry data is invalid: {error}")
    finally:
        connection.close()

    print("PASS: cancellation outcome was settled before the conditional retry decision")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
