#!/usr/bin/env python3
"""Protected verifier for the inventory dependency-planning task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".inventory-runtime" / "inventory.sqlite3"
SEED = ROOT / ".protected" / "inventory_seed.sql"
TOOL = ROOT / "inventoryctl"
GUIDE = ROOT / "INVENTORY.md"
REPORT = ROOT / "inventory-audit.md"
TARGETS = (
    ("Museum Display Hooks", "Exhibit Storage"),
    ("Archival Tissue Paper", "Conservation Lab"),
)

# Fixed after the authored environment is finalized.
EXPECTED_SEED_SHA256 = "ddd2ea45461a5076287e24a53f09f5fa4fb98270ee2c0b01fb84f5e7b65f0de2"
EXPECTED_TOOL_SHA256 = "2fdbafe227197f294ee7ff3b8c5b30ec5e200fd8c86fa4fd95108fd2f8a419c5"
EXPECTED_GUIDE_SHA256 = "435a9b25a2d317f04a100babebf070e54acd9f107fb65ad6f6fb4904725a647a"


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
        "date": row["record_date"],
        "id": row["id"],
        "location": row["location"],
        "name": row["name"],
        "quantity": row["quantity"],
        "status": row["status"],
        "storageNote": row["storage_note"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256,
            "protected inventory seed changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "inventoryctl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "INVENTORY.md changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, status, record_date, quantity, storage_note
                  FROM inventory
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "inventory": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, status, record_date, quantity, storage_note
                      FROM inventory
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
                    """
                    SELECT location, item_family, replenishment_slots
                      FROM availability
                     ORDER BY location, item_family
                    """
                )
            ],
            "notifications": [],
            "mutations": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "inventory-audit.md is missing")
    first, second = records
    expected = (
        "| Record | ID | Date | Status |\n"
        "|---|---|---|---|\n"
        f"| {first['name']} ({first['location']}) | {first['id']} | "
        f"{first['date']} | {first['status']} |\n"
        f"| {second['name']} ({second['location']}) | {second['id']} | "
        f"{second['date']} | {second['status']} |\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "inventory-audit.md does not have the exact ordered two-row table",
    )


def verify_stage(entries: list[sqlite3.Row], operation: str) -> None:
    require(len(entries) == 2, f"expected exactly two {operation} operations")
    require(len({int(entry["pid"]) for entry in entries}) == 2,
            f"the {operation} stage did not use two direct processes")
    require(len({int(entry["parent_pid"]) for entry in entries}) == 1,
            f"the {operation} processes did not share one shell-tool action")
    parent_argv_values = {entry["parent_argv_json"] for entry in entries}
    require(len(parent_argv_values) == 1,
            f"the {operation} processes did not share one action command")
    parent_argv = json.loads(parent_argv_values.pop())
    require(
        isinstance(parent_argv, list)
        and all(isinstance(value, str) for value in parent_argv)
        and len(parent_argv) >= 3,
        f"the {operation} action parent evidence is invalid",
    )
    shell_name = Path(parent_argv[0]).name
    command_indexes = [
        index
        for index, value in enumerate(parent_argv[1:-1], start=1)
        if value.startswith("-") and "c" in value[1:]
    ]
    require(
        shell_name in {"bash", "dash", "ksh", "sh", "zsh"}
        and len(command_indexes) == 1
        and command_indexes[0] + 1 < len(parent_argv),
        f"the {operation} operations were not direct shell-tool processes",
    )
    action_command = parent_argv[command_indexes[0] + 1]
    require(
        action_command.count("./inventoryctl") == 2,
        f"the {operation} action must contain two direct ./inventoryctl commands",
    )
    starts = [entry["started_ns"] for entry in entries]
    finishes = [entry["finished_ns"] for entry in entries]
    require(all(isinstance(value, int) and value > 0 for value in starts + finishes),
            f"the {operation} timing evidence is invalid")
    require(all(start < finish for start, finish in zip(starts, finishes)),
            f"the {operation} timing interval is invalid")
    require(max(starts) < min(finishes),
            f"the two {operation} processes did not overlap")


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the inventory executable environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 4,
                "the workflow must contain exactly two searches and two gets")
        require(
            [event["operation"] for event in events] == ["search", "search", "get", "get"],
            "operations must be two searches immediately followed by two gets",
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

        searches = events[:2]
        gets = events[2:]
        verify_stage(searches, "search")
        verify_stage(gets, "get")
        require(searches[0]["parent_pid"] != gets[0]["parent_pid"],
                "searches and gets must occur in separate shell-tool actions")
        require(
            max(int(event["finished_ns"]) for event in searches)
            < min(int(event["started_ns"]) for event in gets),
            "a get began before both search responses returned",
        )

        expected_by_pair = {
            (record["name"], record["location"]): record for record in records
        }
        searched_ids: dict[tuple[str, str], str] = {}
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(isinstance(arguments, dict)
                    and set(arguments) == {"location", "name"},
                    "a search was not an exact name-and-location search")
            pair = (arguments.get("name"), arguments.get("location"))
            require(pair in expected_by_pair and pair not in searched_ids,
                    "searches did not cover each requested branch exactly once")
            record = expected_by_pair[pair]
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
                "a search did not return its unique requested stable ID",
            )
            searched_ids[pair] = record["id"]
        require(set(searched_ids) == set(expected_by_pair),
                "both requested searches are required")

        ordered_ids = [searched_ids[pair] for pair in TARGETS]
        expected_by_id = {record["id"]: record for record in records}
        gets_by_id: dict[str, sqlite3.Row] = {}
        for event in gets:
            arguments = json.loads(event["arguments_json"])
            require(isinstance(arguments, dict) and set(arguments) == {"id"},
                    "a get must use only one returned stable ID")
            stable_id = arguments.get("id")
            require(stable_id in ordered_ids and stable_id not in gets_by_id,
                    "gets must use each unique searched ID exactly once")
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "a get did not return the required full inventory record",
            )
            gets_by_id[stable_id] = event
        require(set(gets_by_id) == set(ordered_ids),
                "both independently resolved branches must be retrieved")

        current = {
            "inventory": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, status, record_date, quantity, storage_note
                      FROM inventory
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
                    """
                    SELECT location, item_family, replenishment_slots
                      FROM availability
                     ORDER BY location, item_family
                    """
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, inventory_id, message FROM notifications "
                    "ORDER BY sequence"
                )
            ],
            "mutations": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, operation, inventory_id, detail FROM mutation_log "
                    "ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot,
                "inventory, preferences, availability, notification, or mutation state changed")
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
    print("PASS: dependency-gated parallel read-only inventory audit verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
