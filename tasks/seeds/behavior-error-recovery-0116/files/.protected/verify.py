#!/usr/bin/env python3
"""Protected deterministic verifier for the claim error-recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".claim-runtime" / "claims.sqlite3"
SEED = ROOT / ".protected" / "claim_seed.sql"
KEY_PATH = ROOT / ".protected" / "audit.key"
REPORT = ROOT / "claim-availability-report.txt"
RECEIPT = ROOT / ".claim-audit.receipt.json"
TARGETS = (
    ("Theft claim 116", "West Office", "2026-11-17"),
    ("Windshield claim 116", "North Office", "2026-11-17"),
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


def availability_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "availability": row["availability"],
        "claim": row["claim_name"],
        "date": row["availability_date"],
        "office": row["office"],
        "slots_available": row["slots_available"],
    }


def table_state(
    database: sqlite3.Connection,
    query: str,
) -> list[list[Any]]:
    return [list(row) for row in database.execute(query)]


def claims_state(database: sqlite3.Connection) -> list[list[Any]]:
    return table_state(
        database,
        """
        SELECT id, claim_name, office, loss_type, status, claimant, reserve_cents,
               assigned_adjuster
          FROM claims
         ORDER BY id
        """,
    )


def availability_state(database: sqlite3.Connection) -> list[list[Any]]:
    return table_state(
        database,
        """
        SELECT claim_name, office, availability_date, availability,
               slots_available
          FROM claim_availability
         ORDER BY claim_name, office, availability_date
        """,
    )


def mutation_state(database: sqlite3.Connection) -> list[list[Any]]:
    return table_state(
        database,
        """
        SELECT sequence, operation, claim_id, detail
          FROM mutation_log
         ORDER BY sequence
        """,
    )


def transient_state(database: sqlite3.Connection) -> list[list[Any]]:
    return table_state(
        database,
        """
        SELECT claim_name, office, availability_date, remaining
          FROM transient_failures
         ORDER BY claim_name, office, availability_date
        """,
    )


def canonical_fixture() -> tuple[
    list[dict[str, Any]],
    list[list[Any]],
    list[list[Any]],
    list[list[Any]],
    tuple[str, str, str],
]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for claim, office, availability_date in TARGETS:
            rows = source.execute(
                """
                SELECT claim_name, office, availability_date, availability,
                       slots_available
                  FROM claim_availability
                 WHERE claim_name = ? AND office = ? AND availability_date = ?
                """,
                (claim, office, availability_date),
            ).fetchall()
            require(
                len(rows) == 1,
                "each protected claim target must resolve uniquely",
            )
            records.append(availability_record(rows[0]))
        transient_rows = source.execute(
            """
            SELECT claim_name, office, availability_date
              FROM transient_failures
             WHERE remaining = 1
            """
        ).fetchall()
        require(
            len(transient_rows) == 1,
            "protected fixture must designate exactly one transient branch",
        )
        transient = tuple(transient_rows[0])
        require(transient in TARGETS, "transient branch must be a requested target")
        return (
            records,
            claims_state(source),
            availability_state(source),
            mutation_state(source),
            transient,
        )
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
        and set(arguments) == {"claim", "date", "office"},
        "availability operation used an invalid or broad scope",
    )
    values = (arguments["claim"], arguments["office"], arguments["date"])
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


def verify_report(
    records: list[dict[str, Any]],
    transient_scope: tuple[str, str, str],
) -> None:
    require(REPORT.is_file(), "claim-availability-report.txt is missing")
    lines = [
        f"- {record['claim']} | {record['office']} | {record['date']} | "
        f"{record['availability']} | slots {record['slots_available']}"
        for record in records
    ]
    lines.append(
        f"- Retry outcome: {transient_scope[0]} at {transient_scope[1]} "
        "retried once after TRANSIENT_SERVICE_ERROR; succeeded."
    )
    expected = "\n".join(lines) + "\n"
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "claim-availability-report.txt is not the exact grounded three-bullet report",
    )


def verify_receipt(
    rows: list[sqlite3.Row],
    availability_sha256: str,
    claims_sha256: str,
    mutations_sha256: str,
    transient_sha256: str,
) -> None:
    require(RECEIPT.is_file(), "claimctl did not emit its audit receipt")
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
        "availability_sha256": availability_sha256,
        "claims_sha256": claims_sha256,
        "events": [event_payload(row) | {"seal": row["seal"]} for row in rows],
        "mutations_sha256": mutations_sha256,
        "transient_sha256": transient_sha256,
        "version": 1,
    }
    require(payload == expected_payload, "audit receipt does not match genuine execution")


def verify_execution(
    records: list[dict[str, Any]],
    initial_claims: list[list[Any]],
    initial_availability: list[list[Any]],
    initial_mutations: list[list[Any]],
    transient_scope: tuple[str, str, str],
) -> None:
    require(DATABASE.is_file(), "claimctl backing database was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(rows) == 3, "expected exactly two initial checks and one retry")
        require(
            [row["sequence"] for row in rows] == [1, 2, 3],
            "claim operation sequence is incomplete or reordered",
        )
        require(
            all(row["operation"] == "availability" for row in rows),
            "a retrieval, mutation, assignment, or other forbidden operation was used",
        )
        validate_seals(rows)

        initial = rows[:2]
        retry = rows[2]
        initial_scopes = [scope(row) for row in initial]
        require(
            len(set(initial_scopes)) == 2 and set(initial_scopes) == set(TARGETS),
            "the initial phase was not the two exact requested checks",
        )
        require(
            len({row["pid"] for row in initial}) == 2,
            "the initial checks were not separate executable processes",
        )
        initial_intervals = [interval(row) for row in initial]
        require(
            max(item[0] for item in initial_intervals)
            < min(item[1] for item in initial_intervals),
            "the two initial availability checks did not execute concurrently",
        )

        records_by_scope = {
            (record["claim"], record["office"], record["date"]): record
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
            "the successful initial branch was not retained as genuine evidence",
        )
        require(
            failed["outcome"] == "transient_error"
            and failed["error_code"] == "TRANSIENT_SERVICE_ERROR"
            and failed["retryable"] == 1
            and failed["result_digest"] is None,
            "the failed initial branch was not exactly the designated transient error",
        )

        retry_interval = interval(retry)
        require(
            retry_interval[0] > max(item[1] for item in initial_intervals),
            "the retry began before both initial checks completed",
        )
        require(
            scope(retry) == transient_scope,
            "the retry did not target only the transiently failed claim",
        )
        require(
            retry["outcome"] == "ok"
            and retry["error_code"] is None
            and retry["retryable"] is None
            and retry["result_digest"]
            == digest(expected_result(records_by_scope[transient_scope])),
            "the single retry did not produce the required genuine result",
        )

        current_claims = claims_state(database)
        current_availability = availability_state(database)
        current_mutations = mutation_state(database)
        current_transient = transient_state(database)
        require(current_claims == initial_claims, "claim data was changed")
        require(
            current_availability == initial_availability,
            "claim-availability data was changed",
        )
        require(
            current_mutations == initial_mutations == [],
            "a claim mutation or assignment was attempted",
        )
        require(
            len(current_transient) == 1
            and tuple(current_transient[0][:3]) == transient_scope
            and current_transient[0][3] == 0,
            "transient failure budget was not consumed exactly once",
        )
        availability_sha256 = digest(current_availability)
        claims_sha256 = digest(current_claims)
        mutations_sha256 = digest(current_mutations)
        transient_sha256 = digest(current_transient)
    finally:
        database.close()

    verify_receipt(
        rows,
        availability_sha256,
        claims_sha256,
        mutations_sha256,
        transient_sha256,
    )


def main() -> int:
    try:
        (
            records,
            initial_claims,
            initial_availability,
            initial_mutations,
            transient_scope,
        ) = canonical_fixture()
        verify_report(records, transient_scope)
        verify_execution(
            records,
            initial_claims,
            initial_availability,
            initial_mutations,
            transient_scope,
        )
    except (
        OSError,
        ValueError,
        sqlite3.Error,
        json.JSONDecodeError,
        VerificationError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: parallel claim checks and failed-branch-only retry verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
