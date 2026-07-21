#!/usr/bin/env python3
"""Protected deterministic verifier for the account Pi-harness task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".account-runtime" / "accounts.sqlite3"
KEY = ROOT / ".protected" / "audit.key"
SEED = ROOT / ".protected" / "accounts_seed.sql"
SETUP = ROOT / ".protected" / "setup.py"
TOOL = ROOT / "accountctl"
GUIDE = ROOT / "ACCOUNTS.md"
REPORT = ROOT / "account-audit.md"
TARGETS = (
    ("Grove Clinic Partners", "Pacific Region"),
    ("Harbor Books Collective", "Coastal Region"),
)

EXPECTED_SEED_SHA256 = "2bf4eb87efe46649a97cd3baf1e8328e0924d13037e9677756ed2061b0bf5311"
EXPECTED_SETUP_SHA256 = "0f6658ddd3e26b28ada6bafdd4970521f99745951ed1c0e245207d121a118766"
EXPECTED_TOOL_SHA256 = "133fe96fb934ca9dea084457e28efabf18b89ce25bb5639d1eeb846e3d8bce74"
EXPECTED_GUIDE_SHA256 = "09bc04acffebbcafc3ed5ff9d00f09805bbbbc68da5ee6d2d590c6597f5a21a3"
EXPECTED_KEY_SHA256 = "eb4cfb28710bda2f80e42278a9e9a7f4b749929ac31d9de6d6987003b321e147"


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


def full_record(row: sqlite3.Row) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": row["id"],
        "internalNote": row["internal_note"],
        "location": row["location"],
        "name": row["name"],
        "owner": row["owner"],
        "segment": row["segment"],
    }
    if row["account_date"] is not None:
        record["date"] = row["account_date"]
    if row["status"] is not None:
        record["status"] = row["status"]
    return record


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(KEY) == EXPECTED_KEY_SHA256, "protected audit key changed")
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected account seed changed")
    require(file_sha256(SETUP) == EXPECTED_SETUP_SHA256, "protected setup changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "accountctl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "ACCOUNTS.md changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, account_date, status,
                       owner, segment, internal_note
                  FROM accounts
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "accounts": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, account_date, status,
                           owner, segment, internal_note
                      FROM accounts
                     ORDER BY id
                    """
                )
            ],
            "preferences": [
                tuple(row)
                for row in source.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in source.execute(
                    "SELECT location, review_slots FROM availability ORDER BY location"
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def display(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) and value else "unknown"


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "account-audit.md is missing")
    lines = [
        "| Name | ID | Date | Status |",
        "| --- | --- | --- | --- |",
    ]
    for record in records:
        cells = [display(record, key) for key in ("name", "id", "date", "status")]
        lines.append("| " + " | ".join(cells) + " |")

    first_status = display(records[0], "status")
    second_status = display(records[1], "status")
    if "unknown" in {first_status, second_status}:
        comparison = "The status comparison is unavailable."
    elif first_status == second_status:
        comparison = f"The retrieved statuses match: {first_status}."
    else:
        comparison = (
            f"The retrieved statuses differ: {first_status} versus {second_status}."
        )
    expected = "\n".join(lines) + "\n\n" + comparison + "\n"
    observed = REPORT.read_text(encoding="utf-8")
    require(
        observed == expected,
        "account-audit.md does not have the exact ordered, grounded table format",
    )


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the account executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4, "expected exactly four account data operations")
        require(
            [event["operation"] for event in events] == ["search", "search", "get", "get"],
            "operations must be two searches immediately followed by two retrievals",
        )
        require(
            all(
                event["violation"] == 0
                and event["error"] is None
                and event["finished_ns"] is not None
                and event["result_digest"] is not None
                for event in events
            ),
            "a forbidden, failed, incomplete, or ungrounded operation was recorded",
        )

        signing_key = KEY.read_bytes()
        for event in events:
            signed = {
                "arguments_json": event["arguments_json"],
                "error": event["error"],
                "finished_ns": event["finished_ns"],
                "operation": event["operation"],
                "parent_pid": event["parent_pid"],
                "pid": event["pid"],
                "result_count": event["result_count"],
                "result_digest": event["result_digest"],
                "sequence": event["sequence"],
                "sole_id": event["sole_id"],
                "started_ns": event["started_ns"],
                "violation": event["violation"],
            }
            expected_signature = hmac.new(
                signing_key,
                canonical_json(signed).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            require(
                isinstance(event["signature"], str)
                and hmac.compare_digest(event["signature"], expected_signature),
                "execution journal signature is missing or invalid",
            )

        searches = events[:2]
        retrievals = events[2:]
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        search_ids: set[str] = set()
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"location", "name"}, "search used extra inputs")
            account_key = (arguments["name"], arguments["location"])
            require(account_key in expected_by_key, "search targeted an unrequested account")
            require(account_key not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(account_key)
            record = expected_by_key[account_key]
            result = {
                "matches": [
                    {
                        "id": record["id"],
                        "location": record["location"],
                        "name": record["name"],
                    }
                ]
            }
            require(
                event["result_count"] == 1
                and event["sole_id"] == record["id"]
                and event["result_digest"] == digest(result),
                "search did not return the unique requested stable ID",
            )
            search_ids.add(record["id"])
        require(observed_keys == set(expected_by_key), "both exact searches are required")

        expected_by_id = {record["id"]: record for record in records}
        observed_ids: set[str] = set()
        for event in retrievals:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"id"}, "retrieval used extra inputs")
            stable_id = arguments["id"]
            require(stable_id in search_ids, "retrieval ID did not come from a unique search")
            require(stable_id in expected_by_id, "retrieval targeted an unrelated account")
            require(stable_id not in observed_ids, "a resolved branch was retrieved twice")
            observed_ids.add(stable_id)
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "retrieval did not return the required full account record",
            )
        require(observed_ids == set(expected_by_id), "both resolved branches are required")

        require(
            len({event["pid"] for event in searches}) == 2
            and len({event["parent_pid"] for event in searches}) == 1,
            "searches were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in searches)
            < min(event["finished_ns"] for event in searches),
            "the search processes did not overlap",
        )
        require(
            len({event["pid"] for event in retrievals}) == 2
            and len({event["parent_pid"] for event in retrievals}) == 1,
            "retrievals were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in retrievals)
            < min(event["finished_ns"] for event in retrievals),
            "the retrieval processes did not overlap",
        )
        require(
            searches[0]["parent_pid"] != retrievals[0]["parent_pid"],
            "searches and retrievals must occur in separate shell-tool actions",
        )
        require(
            max(event["finished_ns"] for event in searches)
            < min(event["started_ns"] for event in retrievals),
            "retrievals began before both search responses returned",
        )

        current = {
            "accounts": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, account_date, status,
                           owner, segment, internal_note
                      FROM accounts
                     ORDER BY id
                    """
                )
            ],
            "preferences": [
                tuple(row)
                for row in database.execute(
                    "SELECT owner, preference FROM saved_preferences ORDER BY owner"
                )
            ],
            "availability": [
                tuple(row)
                for row in database.execute(
                    "SELECT location, review_slots FROM availability ORDER BY location"
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, account_id, message FROM notifications ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot, "account state changed during the read-only audit")
    finally:
        database.close()


def main() -> int:
    try:
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (OSError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact account audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
