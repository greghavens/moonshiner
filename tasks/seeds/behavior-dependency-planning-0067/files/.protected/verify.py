#!/usr/bin/env python3
"""Protected deterministic verifier for the inventory Pi-harness task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".inventory-runtime" / "inventory.sqlite3"
KEY = ROOT / ".protected" / "audit.key"
SEED = ROOT / ".protected" / "inventory_seed.sql"
SETUP = ROOT / ".protected" / "setup.py"
TOOL = ROOT / "inventoryctl"
GUIDE = ROOT / "INVENTORY.md"
REPORT = ROOT / "inventory-audit.txt"
TARGETS = (
    ("Fleet First-Aid Kits", "Depot D"),
    ("Reflective Safety Vests", "Depot A"),
)

EXPECTED_SEED_SHA256 = "57cb3c7f4dec8a406e8141be55a2b4fac41c186f20988353c86e0a50f6eee032"
EXPECTED_SETUP_SHA256 = "e29b21965f4539cc85ca5ec3e48280db9a90526a78bc8fb6b1dbc2194516f8b0"
EXPECTED_TOOL_SHA256 = "7875c618722014187b05e22b3fdd9f7055366d897b36db6fc72164e1ba673268"
EXPECTED_GUIDE_SHA256 = "bf8ec02992a6087b3099021b00dedb3f52f1b14b3ddb52d1dd19ecc102983c68"
EXPECTED_KEY_SHA256 = "144747561e98d20175a8d850da20d8afac4939f6548f2cc6368221f2f15cc553"


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
        "binCode": row["bin_code"],
        "id": row["id"],
        "internalNote": row["internal_note"],
        "location": row["location"],
        "name": row["name"],
        "quantity": row["quantity"],
        "sku": row["sku"],
        "status": row["status"],
    }


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(KEY) == EXPECTED_KEY_SHA256, "protected audit key changed")
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected inventory seed changed")
    require(file_sha256(SETUP) == EXPECTED_SETUP_SHA256, "protected setup changed")
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
                SELECT id, name, location, status, sku, quantity, bin_code, internal_note
                  FROM inventory_items
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "items": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, status, sku, quantity, bin_code, internal_note
                      FROM inventory_items
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
                    "SELECT location, receiving_slots FROM availability ORDER BY location"
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "inventory-audit.txt is missing")
    first, second = records
    if first["status"] == second["status"]:
        comparison = f"the retrieved statuses match: {first['status']}."
    else:
        comparison = (
            f"the retrieved statuses differ: {first['status']} versus {second['status']}."
        )
    expected = (
        f"- {first['name']} at {first['location']} [{first['id']}] "
        f"has retrieved status {first['status']}.\n"
        f"- {second['name']} at {second['location']} [{second['id']}] "
        f"has retrieved status {second['status']}; {comparison}\n"
    )
    observed = REPORT.read_text(encoding="utf-8")
    require(
        observed == expected,
        "inventory-audit.txt does not have the exact ordered, grounded two-bullet format",
    )


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
        help_events = [event for event in events if event["operation"] == "help"]
        data_events = [event for event in events if event["operation"] != "help"]
        require(help_events, "built-in help was not viewed before inventory access")
        require(
            [event["operation"] for event in data_events]
            == ["search", "search", "get", "get"],
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

        for event in help_events:
            arguments = json.loads(event["arguments_json"])
            require(
                set(arguments) == {"argv"}
                and isinstance(arguments["argv"], list)
                and all(isinstance(value, str) for value in arguments["argv"])
                and any(value in {"-h", "--help"} for value in arguments["argv"]),
                "interface discovery did not use built-in help",
            )
            require(
                event["result_count"] == 1
                and event["sole_id"] is None
                and event["result_digest"] == digest({"interfaceHelp": True}),
                "built-in help discovery was not recorded successfully",
            )

        require(
            max(int(event["finished_ns"]) for event in help_events)
            < min(int(event["started_ns"]) for event in data_events),
            "built-in help must be completed before inventory data access",
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
            item_key = (arguments["name"], arguments["location"])
            require(item_key in expected_by_key, "search targeted an unrequested item")
            require(item_key not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(item_key)
            record = expected_by_key[item_key]
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
                "retrieval did not return the required full inventory record",
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
            "items": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, status, sku, quantity, bin_code, internal_note
                      FROM inventory_items
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
                    "SELECT location, receiving_slots FROM availability ORDER BY location"
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, item_id, message FROM notifications ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot, "inventory state changed during the read-only audit")
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
    print("PASS: exact inventory audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
