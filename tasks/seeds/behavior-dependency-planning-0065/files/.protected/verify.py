#!/usr/bin/env python3
"""Protected deterministic verifier for the support-case Pi-harness task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".case-runtime" / "cases.sqlite3"
KEY = ROOT / ".protected" / "audit.key"
SEED = ROOT / ".protected" / "cases_seed.sql"
SETUP = ROOT / ".protected" / "setup.py"
TOOL = ROOT / "casectl"
GUIDE = ROOT / "CASES.md"
GITIGNORE = ROOT / ".gitignore"
REPORT = ROOT / "case-audit.txt"
TARGETS = (
    ("Mobile Check-In Failure", "Grove Clinics"),
    ("Address Validation Rejection", "Harbor Books"),
)

# Fixed after the authored environment is finalized.
EXPECTED_SEED_SHA256 = "ee8906f7ba85904a63acaa27ac47ed8503c048c4004008ac3728a3cd11f1ebbe"
EXPECTED_SETUP_SHA256 = "ef63939a1e0a343523c2b80600762419b2ea7ca83d90da084e112965dc37961e"
EXPECTED_TOOL_SHA256 = "6ec8358cd8d6efece18c77a2085b1b603658ba70786cbd0284b626c1ec247f14"
EXPECTED_GUIDE_SHA256 = "d3e3625aac2baeaca9cfa49baf79fb4e5945a7593e65524c44a56f5b530d3f10"
EXPECTED_KEY_SHA256 = "942acd72847a9d13d2b0e4e423e9623280b26025c76b7ecc8696083d4aefa85e"
EXPECTED_GITIGNORE_SHA256 = "8013288265b880f62688779cfa691204312208ae2ec245a0f9b8191b53b14bea"


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
    return {
        "id": row["id"],
        "internalNote": row["internal_note"],
        "location": row["location"],
        "name": row["name"],
        "openedDate": row["opened_date"],
        "owner": row["owner"],
        "priority": row["priority"],
        "status": row["status"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(KEY) == EXPECTED_KEY_SHA256, "protected audit key changed")
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected case seed changed")
    require(file_sha256(SETUP) == EXPECTED_SETUP_SHA256, "protected setup changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "casectl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "CASES.md changed")
    require(file_sha256(GITIGNORE) == EXPECTED_GITIGNORE_SHA256, ".gitignore changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, status, opened_date,
                       priority, owner, internal_note
                  FROM cases
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "cases": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, status, opened_date,
                           priority, owner, internal_note
                      FROM cases
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
                    "SELECT location, responder_count FROM availability ORDER BY location"
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "case-audit.txt is missing")
    first, second = records
    if first["status"] == second["status"]:
        comparison = f"The retrieved statuses match: {first['status']}."
    else:
        comparison = (
            f"The retrieved statuses differ: {first['status']} versus {second['status']}."
        )
    expected = (
        f"{first['name']} at {first['location']} [{first['id']}] "
        f"has retrieved status {first['status']}.\n"
        f"{second['name']} at {second['location']} [{second['id']}] "
        f"has retrieved status {second['status']}.\n"
        f"{comparison}\n"
        "No changes made.\n"
    )
    observed = REPORT.read_text(encoding="utf-8")
    require(
        observed == expected,
        "case-audit.txt does not have the exact ordered, grounded format",
    )
    require(len(observed.split()) < 75, "case-audit.txt is not under 75 words")


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the support case executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        help_count = 0
        while help_count < len(events) and events[help_count]["operation"] == "help":
            help_count += 1
        help_events = events[:help_count]
        data_events = events[help_count:]
        require(help_events, "built-in interface help was not used before case data")
        require(
            [event["operation"] for event in data_events]
            == ["search", "search", "get", "get"],
            "interface discovery must precede two searches immediately followed by two retrievals",
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
        key = KEY.read_bytes()
        for event in events:
            signed = {
                "arguments_json": event["arguments_json"],
                "error": event["error"],
                "finished_ns": event["finished_ns"],
                "operation": event["operation"],
                "parent_exe": event["parent_exe"],
                "parent_pid": event["parent_pid"],
                "parent_started_ticks": event["parent_started_ticks"],
                "pid": event["pid"],
                "result_count": event["result_count"],
                "result_digest": event["result_digest"],
                "sequence": event["sequence"],
                "sole_id": event["sole_id"],
                "started_ns": event["started_ns"],
                "violation": event["violation"],
            }
            expected_signature = hmac.new(
                key, canonical_json(signed).encode("utf-8"), hashlib.sha256
            ).hexdigest()
            require(
                isinstance(event["signature"], str)
                and hmac.compare_digest(event["signature"], expected_signature),
                "execution journal signature is missing or invalid",
            )

        for help_event in help_events:
            help_arguments = json.loads(help_event["arguments_json"])
            help_argv = (
                help_arguments.get("argv")
                if isinstance(help_arguments, dict)
                else None
            )
            require(
                isinstance(help_arguments, dict)
                and set(help_arguments) == {"argv"}
                and isinstance(help_argv, list)
                and all(isinstance(argument, str) for argument in help_argv)
                and any(argument in {"-h", "--help"} for argument in help_argv)
                and help_event["result_count"] == 1
                and help_event["sole_id"] is None
                and help_event["result_digest"] == digest({"interface": "casectl"}),
                "a recorded interface-discovery event was not genuine built-in help",
            )

        searches = data_events[:2]
        retrievals = data_events[2:]
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"location", "name"}, "search used extra inputs")
            key = (arguments["name"], arguments["location"])
            require(key in expected_by_key, "search targeted an unrequested case")
            require(key not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(key)
            record = expected_by_key[key]
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
        require(observed_keys == set(expected_by_key), "both exact searches are required")

        expected_by_id = {record["id"]: record for record in records}
        observed_ids: set[str] = set()
        for event in retrievals:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"id"}, "retrieval used extra inputs")
            stable_id = arguments["id"]
            require(stable_id in expected_by_id, "retrieval did not use a resolved target ID")
            require(stable_id not in observed_ids, "a resolved branch was retrieved twice")
            observed_ids.add(stable_id)
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "retrieval did not return the required full case record",
            )
        require(observed_ids == set(expected_by_id), "both resolved branches are required")

        require(
            len({event["pid"] for event in searches}) == 2
            and all(event["parent_started_ticks"] >= 0 for event in searches)
            and len(
                {
                    (event["parent_pid"], event["parent_started_ticks"])
                    for event in searches
                }
            )
            == 1
            and all(event["parent_exe"] in {"bash", "dash", "sh", "zsh"} for event in searches),
            "searches were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in searches)
            < min(event["finished_ns"] for event in searches),
            "the search processes did not overlap",
        )
        require(
            len({event["pid"] for event in retrievals}) == 2
            and all(event["parent_started_ticks"] >= 0 for event in retrievals)
            and len(
                {
                    (event["parent_pid"], event["parent_started_ticks"])
                    for event in retrievals
                }
            )
            == 1
            and all(
                event["parent_exe"] in {"bash", "dash", "sh", "zsh"}
                for event in retrievals
            ),
            "retrievals were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in retrievals)
            < min(event["finished_ns"] for event in retrievals),
            "the retrieval processes did not overlap",
        )
        require(
            (
                searches[0]["parent_pid"],
                searches[0]["parent_started_ticks"],
            )
            != (
                retrievals[0]["parent_pid"],
                retrievals[0]["parent_started_ticks"],
            ),
            "searches and retrievals must occur in separate shell-tool actions",
        )
        require(
            max(event["finished_ns"] for event in searches)
            < min(event["started_ns"] for event in retrievals),
            "retrievals began before both search responses returned",
        )

        current = {
            "cases": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, status, opened_date,
                           priority, owner, internal_note
                      FROM cases
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
                    "SELECT location, responder_count FROM availability ORDER BY location"
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, case_id, message FROM notifications ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot, "support case state changed during the read-only audit")
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
    print("PASS: exact case audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
