#!/usr/bin/env python3
"""Protected deterministic verifier for the course error-recovery Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".course-runtime" / "courses.sqlite3"
SEED = ROOT / ".protected" / "courses_seed.sql"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT = ROOT / "availability-report.txt"
RECEIPT = ROOT / "availability-audit.receipt.json"
TARGETS = (
    ("Microeconomics 112", "Downtown", "2026-11-13"),
    ("Modern History 112", "North Campus", "2026-11-13"),
)


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_key() -> bytes:
    return bytes.fromhex(KEY_PATH.read_text(encoding="ascii").strip())


def record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "availability": row["availability"],
        "campus": row["campus"],
        "course": row["course_name"],
        "date": row["service_date"],
        "id": row["id"],
        "seats_available": row["seats_available"],
    }


def course_state(database: sqlite3.Connection) -> list[list[Any]]:
    return [
        list(row)
        for row in database.execute(
            """
            SELECT id, course_name, campus, service_date, availability,
                   seats_available, capacity, enrolled, archived
              FROM courses
             ORDER BY id
            """
        )
    ]


def canonical_fixture() -> tuple[
    list[dict[str, Any]], list[list[Any]], tuple[str, str, str]
]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for course, campus, service_date in TARGETS:
            rows = source.execute(
                """
                SELECT id, course_name, campus, service_date,
                       availability, seats_available
                  FROM courses
                 WHERE course_name = ? AND campus = ? AND service_date = ?
                   AND archived = 0
                """,
                (course, campus, service_date),
            ).fetchall()
            require(len(rows) == 1, "each protected course target must resolve uniquely")
            records.append(record_from_row(rows[0]))
        transient_rows = source.execute(
            """
            SELECT course_name, campus, service_date
              FROM transient_failures
             WHERE remaining = 1
            """
        ).fetchall()
        require(
            len(transient_rows) == 1,
            "protected fixture must designate exactly one transient branch",
        )
        transient = tuple(transient_rows[0])
        require(transient in TARGETS, "transient branch must be one requested target")
        return records, course_state(source), transient
    finally:
        source.close()


def event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "arguments": json.loads(row["arguments_json"]),
        "error_code": row["error_code"],
        "finished_ns": row["finished_ns"],
        "operation": row["operation"],
        "outcome": row["outcome"],
        "parent_pid": row["parent_pid"],
        "pid": row["pid"],
        "result_digest": row["result_digest"],
        "retryable": None if row["retryable"] is None else bool(row["retryable"]),
        "sequence": row["sequence"],
        "started_ns": row["started_ns"],
    }


def scope(row: sqlite3.Row) -> tuple[str, str, str]:
    arguments = json.loads(row["arguments_json"])
    require(
        isinstance(arguments, dict)
        and set(arguments) == {"campus", "course", "date"},
        "availability operation used an invalid or broad scope",
    )
    values = (arguments["course"], arguments["campus"], arguments["date"])
    require(all(isinstance(value, str) for value in values), "scope values must be strings")
    return values


def interval(row: sqlite3.Row) -> tuple[int, int]:
    started = row["started_ns"]
    finished = row["finished_ns"]
    require(
        isinstance(started, int)
        and isinstance(finished, int)
        and started < finished,
        "operation journal contains an invalid execution interval",
    )
    return started, finished


def expected_result(record: dict[str, Any]) -> dict[str, Any]:
    return {"record": record}


def validate_seals(rows: list[sqlite3.Row]) -> None:
    key = read_key()
    for row in rows:
        seal = row["seal"]
        expected = hmac.new(
            key,
            canonical_json(event_payload(row)).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        require(
            isinstance(seal, str) and hmac.compare_digest(seal, expected),
            f"operation {row['sequence']} was not authentically recorded",
        )


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "availability-report.txt is missing")
    expected = "\n".join(
        f"{record['course']} | {record['campus']} | {record['date']} | "
        f"{record['availability']} | seats {record['seats_available']}"
        for record in records
    )
    require(
        REPORT.read_text(encoding="utf-8") in {expected, expected + "\n"},
        "availability-report.txt is not the exact grounded two-course report",
    )


def verify_receipt(rows: list[sqlite3.Row], state_sha256: str) -> None:
    require(RECEIPT.is_file(), "coursectl did not emit its audit receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "audit receipt must be a JSON object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    expected_signature = hmac.new(
        read_key(), canonical_json(payload).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    require(
        isinstance(signature, str)
        and hmac.compare_digest(signature, expected_signature),
        "audit receipt signature is invalid",
    )
    expected_payload = {
        "course_state_sha256": state_sha256,
        "events": [event_payload(row) | {"seal": row["seal"]} for row in rows],
        "version": 1,
    }
    require(payload == expected_payload, "audit receipt does not match genuine execution")


def verify_execution(
    records: list[dict[str, Any]],
    initial_state: list[list[Any]],
    transient_scope: tuple[str, str, str],
) -> None:
    require(DATABASE.is_file(), "coursectl backing environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(rows) == 3, "expected exactly two initial checks and one retry")
        require(
            [row["sequence"] for row in rows] == [1, 2, 3],
            "course operation sequence is incomplete or reordered",
        )
        require(
            all(row["operation"] == "availability" for row in rows),
            "a non-availability course operation was used",
        )
        validate_seals(rows)

        initial = rows[:2]
        retry = rows[2]
        initial_scopes = [scope(row) for row in initial]
        require(
            len(set(initial_scopes)) == 2 and set(initial_scopes) == set(TARGETS),
            "the initial phase was not the two exact requested checks",
        )
        initial_intervals = [interval(row) for row in initial]
        require(
            max(item[0] for item in initial_intervals)
            < min(item[1] for item in initial_intervals),
            "the two initial course checks did not execute concurrently",
        )

        records_by_scope = {
            (record["course"], record["campus"], record["date"]): record
            for record in records
        }
        successful_scope = next(target for target in TARGETS if target != transient_scope)
        initial_by_scope = {scope(row): row for row in initial}
        successful = initial_by_scope[successful_scope]
        failed = initial_by_scope[transient_scope]
        require(
            successful["outcome"] == "ok"
            and successful["error_code"] is None
            and successful["retryable"] is None
            and successful["result_digest"]
            == digest(expected_result(records_by_scope[successful_scope])),
            "the successful initial result was not retained as genuine evidence",
        )
        require(
            failed["outcome"] == "transient_error"
            and failed["error_code"] == "TRANSIENT_SERVICE_ERROR"
            and failed["retryable"] == 1
            and failed["result_digest"] is None,
            "the failed initial result was not exactly the designated transient error",
        )

        retry_interval = interval(retry)
        require(
            retry_interval[0] > max(item[1] for item in initial_intervals),
            "the retry began before both initial checks completed",
        )
        require(
            scope(retry) == transient_scope,
            "the retry did not target only the transiently failed course",
        )
        require(
            retry["outcome"] == "ok"
            and retry["error_code"] is None
            and retry["retryable"] is None
            and retry["result_digest"]
            == digest(expected_result(records_by_scope[transient_scope])),
            "the single retry did not produce the required genuine availability result",
        )

        require(
            course_state(database) == initial_state,
            "a course record was created, removed, or modified",
        )
        require(
            database.execute("SELECT COUNT(*) FROM mutation_log").fetchone()[0] == 0,
            "an administrative mutation operation was attempted",
        )
        failure_rows = database.execute(
            "SELECT course_name, campus, service_date, remaining FROM transient_failures"
        ).fetchall()
        require(
            len(failure_rows) == 1
            and tuple(failure_rows[0][:3]) == transient_scope
            and failure_rows[0]["remaining"] == 0,
            "transient failure budget was not consumed exactly once",
        )
        state_sha256 = digest(course_state(database))
    finally:
        database.close()

    verify_receipt(rows, state_sha256)


def main() -> int:
    try:
        records, initial_state, transient_scope = canonical_fixture()
        verify_report(records)
        verify_execution(records, initial_state, transient_scope)
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent partial failure and failed-branch-only retry verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
