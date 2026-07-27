#!/usr/bin/env python3
"""Protected deterministic verifier for the shipment recovery task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".shipment-runtime" / "shipments.sqlite3"
KEY = ROOT / ".protected" / "audit.key"
SEED = ROOT / ".protected" / "shipment_seed.sql"
SETUP = ROOT / ".protected" / "setup.py"
TOOL = ROOT / "shipmentctl"
GUIDE = ROOT / "SHIPMENTS.md"
REPORT = ROOT / "shipment-availability.md"
TARGETS = (
    ("Library transfer cartons", "Portland", "2026-08-12"),
    ("Vaccine cooler shipment", "Denver", "2026-08-12"),
)

EXPECTED_KEY_SHA256 = "d50bcca79d32ef04f3dc0b69b329f9c3c8e9280c0ccfb76e4c2a7c195c7a9535"
EXPECTED_SEED_SHA256 = "b00f373aabfafd3bcc3b551516e021252bc0251e7ec9c1197b39a6c25c20c593"
EXPECTED_SETUP_SHA256 = "d041dfcc326c7a7c9533781a9a2a965addb84021161e03de25a41775d8517377"
EXPECTED_TOOL_SHA256 = "6c861198a86cd02111479f1f15ac63c075c868128319e445b6e68a2a1e3bab8a"
EXPECTED_GUIDE_SHA256 = "1bf0ca42817c5ed293a8b44b94305f54f63bf48023269030e5c83d664e6205b1"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def target_from_event(event: sqlite3.Row) -> tuple[str, str, str]:
    arguments = json.loads(event["arguments_json"])
    require(set(arguments) == {"date", "location", "name"}, "check used extra inputs")
    require(
        all(isinstance(arguments[key], str) for key in ("name", "location", "date")),
        "check inputs must be strings",
    )
    return (arguments["name"], arguments["location"], arguments["date"])


def result_for(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "available": bool(row["available"]),
        "date": row["ship_date"],
        "location": row["location"],
        "name": row["name"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], list[tuple[Any, ...]]]:
    require(file_sha256(KEY) == EXPECTED_KEY_SHA256, "protected audit key changed")
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected shipment seed changed")
    require(file_sha256(SETUP) == EXPECTED_SETUP_SHA256, "protected setup changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "shipmentctl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "SHIPMENTS.md changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location, date in TARGETS:
            rows = source.execute(
                """
                SELECT name, location, ship_date, available
                  FROM shipments
                 WHERE name = ? AND location = ? AND ship_date = ?
                """,
                (name, location, date),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(result_for(rows[0]))
        snapshot = [
            tuple(row)
            for row in source.execute(
                """
                SELECT id, name, location, ship_date, available, carrier, internal_note
                  FROM shipments
                 ORDER BY id
                """
            )
        ]
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]], retry_record: dict[str, Any]) -> None:
    require(REPORT.is_file(), "shipment-availability.md is missing")
    lines = []
    for record in records:
        available = "true" if record["available"] else "false"
        lines.append(
            f"- {record['name']} | {record['location']} | {record['date']} | "
            f"available: {available}."
        )
    lines.append(
        f"- Retried: {retry_record['name']} at {retry_record['location']} "
        "once after a transient error."
    )
    expected = "\n".join(lines) + "\n"
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "shipment-availability.md does not have the exact grounded three-bullet format",
    )


def verify_signature(event: sqlite3.Row, signing_key: bytes) -> None:
    signed = {
        "arguments_json": event["arguments_json"],
        "error": event["error"],
        "finished_ns": event["finished_ns"],
        "operation": event["operation"],
        "outcome": event["outcome"],
        "namespace_id": event["namespace_id"],
        "parent_pid": event["parent_pid"],
        "pid": event["pid"],
        "result_count": event["result_count"],
        "result_digest": event["result_digest"],
        "sequence": event["sequence"],
        "started_ns": event["started_ns"],
    }
    expected = hmac.new(
        signing_key, canonical_json(signed).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    require(
        isinstance(event["signature"], str)
        and hmac.compare_digest(event["signature"], expected),
        "execution journal signature is missing or invalid",
    )


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: list[tuple[Any, ...]],
) -> dict[str, Any]:
    require(DATABASE.is_file(), "the shipment executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(events, "the shipment executable was not used")
        require(
            all(
                event["finished_ns"] is not None
                and event["outcome"] is not None
                and event["signature"] is not None
                for event in events
            ),
            "an incomplete shipment operation was recorded",
        )
        signing_key = KEY.read_bytes()
        for event in events:
            verify_signature(event, signing_key)

        help_events = [event for event in events if event["operation"] == "help"]
        data_events = [event for event in events if event["operation"] != "help"]
        require(help_events, "built-in help was not viewed before shipment access")
        require(
            all(event["operation"] == "availability" for event in data_events),
            "a non-availability shipment operation was invoked",
        )
        require(
            len(data_events) == 3,
            "execution must contain two initial checks and one eligible retry",
        )

        for event in help_events:
            arguments = json.loads(event["arguments_json"])
            require(
                set(arguments) == {"argv"}
                and isinstance(arguments["argv"], list)
                and arguments["argv"] == ["--help"],
                "interface discovery did not use exact ./shipmentctl --help",
            )
            require(
                event["outcome"] == "success"
                and event["error"] is None
                and event["result_count"] == 1
                and event["result_digest"] == digest({"interfaceHelp": True}),
                "built-in help discovery was not recorded successfully",
            )
        require(
            max(int(event["finished_ns"]) for event in help_events)
            < min(int(event["started_ns"]) for event in data_events),
            "built-in help must finish before shipment data access",
        )

        initial = data_events[:2]
        retry = data_events[2]
        initial_by_target: dict[tuple[str, str, str], sqlite3.Row] = {}
        for event in initial:
            target = target_from_event(event)
            require(target in TARGETS, "initial check targeted an unrequested shipment")
            require(target not in initial_by_target, "an initial branch was repeated")
            initial_by_target[target] = event
        require(set(initial_by_target) == set(TARGETS), "both exact initial checks are required")

        require(
            len({event["pid"] for event in initial}) == 2
            and len({event["parent_pid"] for event in initial}) == 1
            and len({event["namespace_id"] for event in initial}) == 1,
            "initial checks were not separate direct processes from one shell-tool action",
        )
        require(
            max(int(event["started_ns"]) for event in initial)
            < min(int(event["finished_ns"]) for event in initial),
            "the two initial check processes did not overlap",
        )

        successful = [event for event in initial if event["outcome"] == "success"]
        transient = [
            event
            for event in initial
            if event["outcome"] == "transient_error"
            and event["error"] == "rate_limited"
        ]
        require(
            len(successful) == 1 and len(transient) == 1,
            "the initial partial failure was not preserved exactly",
        )
        require(
            successful[0]["error"] is None and successful[0]["result_count"] == 1,
            "the successful initial branch was not recorded cleanly",
        )
        require(
            transient[0]["result_count"] is None
            and transient[0]["result_digest"] is None,
            "the transient branch incorrectly recorded a successful result",
        )

        expected_by_target = {TARGETS[index]: records[index] for index in range(2)}
        successful_target = target_from_event(successful[0])
        require(
            successful[0]["result_digest"] == digest(expected_by_target[successful_target]),
            "the successful initial result was not grounded in shipment data",
        )

        retry_target = target_from_event(retry)
        transient_target = target_from_event(transient[0])
        require(
            retry_target == transient_target,
            "the retry did not target only the transiently failed branch",
        )
        require(
            retry_target != successful_target,
            "the successful initial branch was repeated",
        )
        require(
            retry["parent_pid"] != initial[0]["parent_pid"]
            or retry["namespace_id"] != initial[0]["namespace_id"],
            "the retry was not issued in a later shell-tool action",
        )
        require(
            int(retry["started_ns"])
            > max(int(event["finished_ns"]) for event in initial),
            "the retry began before both initial results returned",
        )
        require(
            retry["outcome"] == "success"
            and retry["error"] is None
            and retry["result_count"] == 1
            and retry["result_digest"] == digest(expected_by_target[retry_target]),
            "the one permitted retry did not return the grounded branch result",
        )

        current = [
            tuple(row)
            for row in database.execute(
                """
                SELECT id, name, location, ship_date, available, carrier, internal_note
                  FROM shipments
                 ORDER BY id
                """
            )
        ]
        require(current == snapshot, "shipment state changed during the read-only checks")
        return expected_by_target[retry_target]
    finally:
        database.close()


def main() -> int:
    try:
        records, snapshot = canonical_state()
        retry_record = verify_execution(records, snapshot)
        verify_report(records, retry_record)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact report and concurrent partial-failure recovery verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
