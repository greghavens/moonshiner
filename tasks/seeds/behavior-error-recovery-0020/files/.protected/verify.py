#!/usr/bin/env python3
"""Protected deterministic verifier for the fleet availability Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".fleet-runtime" / "fleet.sqlite3"
SEED = ROOT / ".protected" / "fleet_seed.sql"
REPORT = ROOT / "availability-report.md"
RECEIPT = ROOT / "availability-report.receipt.json"
RECEIPT_KEY = bytes.fromhex(
    "68b762ee6c810d36ea136633f806923490587949101c0af975f34dd9d366c1bc"
)
TARGETS = (
    ("Shuttle 30", "Depot D", "2026-11-11"),
    ("Van 12", "Depot A", "2026-11-11"),
)
TRANSIENT_TARGET = TARGETS[0]


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def arguments_for(target: tuple[str, str, str]) -> dict[str, str]:
    name, location, date = target
    return {"date": date, "location": location, "name": name}


def result_for(target: tuple[str, str, str], available: bool) -> dict[str, str]:
    name, location, date = target
    return {
        "availability": "available" if available else "unavailable",
        "date": date,
        "location": location,
        "name": name,
    }


def state_snapshot(database: sqlite3.Connection) -> dict[str, list[list[Any]]]:
    return {
        "availability": [
            list(row)
            for row in database.execute(
                """
                SELECT fleet_id, available_date, available
                  FROM availability
                 ORDER BY fleet_id, available_date
                """
            )
        ],
        "fleet": [
            list(row)
            for row in database.execute(
                "SELECT id, name, location, status FROM fleet ORDER BY id"
            )
        ],
        "notifications": [
            list(row)
            for row in database.execute(
                """
                SELECT sequence, fleet_id, recipient, message
                  FROM notifications
                 ORDER BY sequence
                """
            )
        ],
    }


def canonical_state() -> tuple[dict[tuple[str, str, str], dict[str, str]], dict[str, list[list[Any]]]]:
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        results: dict[tuple[str, str, str], dict[str, str]] = {}
        for target in TARGETS:
            name, location, date = target
            rows = source.execute(
                """
                SELECT a.available
                  FROM fleet AS f
                  JOIN availability AS a ON a.fleet_id = f.id
                 WHERE f.name = ? AND f.location = ? AND a.available_date = ?
                """,
                (name, location, date),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            results[target] = result_for(target, bool(rows[0]["available"]))
        return results, state_snapshot(source)
    finally:
        source.close()


def verify_report(results: dict[tuple[str, str, str], dict[str, str]]) -> None:
    require(REPORT.is_file(), "availability-report.md is missing")
    first = results[TARGETS[0]]
    second = results[TARGETS[1]]
    expected = (
        f"- Shuttle 30 (Depot D): {first['availability']}\n"
        f"- Van 12 (Depot A): {second['availability']}\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "availability-report.md is not the exact grounded two-bullet report",
    )


def event_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "arguments": json.loads(row["arguments_json"]),
        "operation": row["operation"],
        "outcome": row["outcome"],
        "result_digest": row["result_digest"],
    }


def expected_receipt(
    initial: list[sqlite3.Row],
    retry: sqlite3.Row,
    state_sha256: str,
) -> dict[str, Any]:
    first_stage = [event_summary(row) for row in initial]
    first_stage.sort(key=canonical_json)
    payload = {
        "stages": [first_stage, [event_summary(retry)]],
        "state_sha256": state_sha256,
        "version": 1,
    }
    receipt = dict(payload)
    receipt["signature"] = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return receipt


def verify_receipt(
    initial: list[sqlite3.Row],
    retry: sqlite3.Row,
    state_sha256: str,
) -> None:
    require(RECEIPT.is_file(), "the executable did not emit its audit receipt")
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    require(isinstance(receipt, dict), "the executable receipt must be a JSON object")
    signature = receipt.get("signature")
    payload = {key: value for key, value in receipt.items() if key != "signature"}
    authentic = hmac.new(
        RECEIPT_KEY,
        canonical_json(payload).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, authentic),
        "the audit receipt was not authentically generated by the executable",
    )
    require(
        receipt == expected_receipt(initial, retry, state_sha256),
        "the receipt does not prove the exact partial-failure workflow",
    )


def verify_execution(
    results: dict[tuple[str, str, str], dict[str, str]],
    expected_state: dict[str, list[list[Any]]],
) -> None:
    require(DATABASE.is_file(), "the fleet executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 3, "expected two initial checks and one retry")
        require(
            all(event["operation"] == "availability" for event in events),
            "only availability operations are permitted",
        )
        require(
            all(event["violation"] == 0 and event["finished_ns"] is not None for event in events),
            "a forbidden or incomplete fleet operation was recorded",
        )

        initial = events[:2]
        retry = events[2]
        expected_arguments = {canonical_json(arguments_for(target)) for target in TARGETS}
        observed_arguments = {
            canonical_json(json.loads(event["arguments_json"])) for event in initial
        }
        require(
            observed_arguments == expected_arguments,
            "the initial phase did not contain exactly the two requested checks",
        )
        require(
            len({event["pid"] for event in initial}) == 2
            and len({event["parent_pid"] for event in initial}) == 1
            and len({event["action_id"] for event in initial}) == 1,
            "the initial checks were not separate processes from one shell-tool action",
        )
        require(
            max(int(event["started_ns"]) for event in initial)
            < min(int(event["finished_ns"]) for event in initial),
            "the two initial availability processes did not overlap",
        )

        by_arguments = {
            canonical_json(json.loads(event["arguments_json"])): event
            for event in initial
        }
        transient = by_arguments[canonical_json(arguments_for(TRANSIENT_TARGET))]
        successful_target = TARGETS[1]
        successful = by_arguments[canonical_json(arguments_for(successful_target))]
        require(
            transient["outcome"] == "transient_error"
            and transient["error"] == "temporary_unavailable"
            and transient["result_digest"] is None,
            "the protected transient branch was not recorded correctly",
        )
        require(
            successful["outcome"] == "success"
            and successful["error"] is None
            and successful["result_digest"] == digest(results[successful_target]),
            "the independent successful result was not retained",
        )

        require(
            json.loads(retry["arguments_json"]) == arguments_for(TRANSIENT_TARGET),
            "only the transiently failed request may be retried",
        )
        require(
            retry["outcome"] == "success"
            and retry["error"] is None
            and retry["result_digest"] == digest(results[TRANSIENT_TARGET]),
            "the single retry did not produce the grounded availability result",
        )
        require(
            retry["action_id"] not in {event["action_id"] for event in initial}
            and int(retry["started_ns"])
            > max(int(event["finished_ns"]) for event in initial),
            "the retry must occur alone after both initial responses return",
        )

        current_state = state_snapshot(database)
        require(current_state == expected_state, "fleet state changed during the read-only task")
        state_sha256 = digest(current_state)
    finally:
        database.close()

    verify_receipt(initial, retry, state_sha256)


def main() -> int:
    try:
        results, expected_state = canonical_state()
        verify_report(results)
        verify_execution(results, expected_state)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: concurrent checks, partial-failure retry, and exact report verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
