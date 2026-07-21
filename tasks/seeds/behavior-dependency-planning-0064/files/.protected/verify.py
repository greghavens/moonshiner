#!/usr/bin/env python3
"""Protected deterministic verifier for the outbound-message Pi task."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".message-runtime" / "messages.sqlite3"
KEY_FILE = ROOT / ".protected" / "audit.key"
SEED = ROOT / ".protected" / "messages_seed.sql"
SETUP = ROOT / ".protected" / "setup.py"
TOOL = ROOT / "messagectl"
GUIDE = ROOT / "MESSAGES.md"
GITIGNORE = ROOT / ".gitignore"
REPORT = ROOT / "message-audit.md"
TARGETS = (
    ("Donor Reception Follow-Up", "Advancement"),
    ("Exhibit Opening Invitation", "Museum Members"),
)

# Fixed after the authored environment is finalized.
EXPECTED_KEY_SHA256 = "3523f137c963c938c557e8a575f2664b976eea9b566e95e1b445b83038011cc2"
EXPECTED_SEED_SHA256 = "b2ed7ed2a8a9c1da382c26abe867925b19a4e0f3437e07d001f964f3a3c24596"
EXPECTED_SETUP_SHA256 = "bf92a28046b32a879bef29807ade9d9e331a75d27c337d1ee3eb88fbaed3ed5b"
EXPECTED_TOOL_SHA256 = "813eb75210bcc7ec235f52619c8ec130e3cef735e97a9788bf994d771fb0f43d"
EXPECTED_GUIDE_SHA256 = "d6652d9c61034ddf2c44b742c706205dc63dce8202dcd0aaca030b5c400321b2"
EXPECTED_GITIGNORE_SHA256 = "530afbf55d1703f42df91168ca586a167f3e8153d211b6c5d36f9e9879523e8a"


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
        "channel": row["channel"],
        "date": row["send_date"],
        "id": row["id"],
        "internalNote": row["internal_note"],
        "location": row["location"],
        "name": row["name"],
        "owner": row["owner"],
        "status": row["status"],
    }


def signature_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "arguments_json": row["arguments_json"],
        "error": row["error"],
        "finished_ns": row["finished_ns"],
        "operation": row["operation"],
        "parent_exe": row["parent_exe"],
        "parent_pid": row["parent_pid"],
        "pid": row["pid"],
        "result_count": row["result_count"],
        "result_digest": row["result_digest"],
        "sequence": row["sequence"],
        "sole_id": row["sole_id"],
        "started_ns": row["started_ns"],
        "violation": row["violation"],
    }


def event_signature(row: sqlite3.Row, key: bytes) -> str:
    return hmac.new(
        key,
        canonical_json(signature_payload(row)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def canonical_state() -> tuple[list[dict[str, Any]], dict[str, list[tuple[Any, ...]]]]:
    require(file_sha256(KEY_FILE) == EXPECTED_KEY_SHA256, "protected audit key changed")
    require(file_sha256(SEED) == EXPECTED_SEED_SHA256, "protected message seed changed")
    require(file_sha256(SETUP) == EXPECTED_SETUP_SHA256, "protected setup changed")
    require(file_sha256(TOOL) == EXPECTED_TOOL_SHA256, "messagectl changed")
    require(file_sha256(GUIDE) == EXPECTED_GUIDE_SHA256, "MESSAGES.md changed")
    require(file_sha256(GITIGNORE) == EXPECTED_GITIGNORE_SHA256, ".gitignore changed")

    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    try:
        source.executescript(SEED.read_text(encoding="utf-8"))
        records: list[dict[str, Any]] = []
        for name, location in TARGETS:
            rows = source.execute(
                """
                SELECT id, name, location, send_date, status, channel, owner,
                       internal_note
                  FROM messages
                 WHERE name = ? AND location = ?
                 ORDER BY id
                """,
                (name, location),
            ).fetchall()
            require(len(rows) == 1, "each protected target must resolve uniquely")
            records.append(full_record(rows[0]))
        snapshot = {
            "messages": [
                tuple(row)
                for row in source.execute(
                    """
                    SELECT id, name, location, send_date, status, channel, owner,
                           internal_note
                      FROM messages
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
                    SELECT location, send_date, capacity
                      FROM availability
                     ORDER BY location, send_date
                    """
                )
            ],
            "notifications": [],
        }
        return records, snapshot
    finally:
        source.close()


def verify_report(records: list[dict[str, Any]]) -> None:
    require(REPORT.is_file(), "message-audit.md is missing")
    expected = (
        "| Record | ID | Date | Status |\n"
        "|---|---|---|---|\n"
        f"| {records[0]['name']} | {records[0]['id']} | "
        f"{records[0]['date']} | {records[0]['status']} |\n"
        f"| {records[1]['name']} | {records[1]['id']} | "
        f"{records[1]['date']} | {records[1]['status']} |\n"
    )
    require(
        REPORT.read_text(encoding="utf-8") == expected,
        "message-audit.md does not have the exact ordered, grounded table",
    )


def verify_execution(
    records: list[dict[str, Any]],
    snapshot: dict[str, list[tuple[Any, ...]]],
) -> None:
    require(DATABASE.is_file(), "the message executable environment was not used")
    key = bytes.fromhex(KEY_FILE.read_text(encoding="ascii").strip())
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        events = database.execute(
            "SELECT * FROM operation_journal ORDER BY sequence"
        ).fetchall()
        require(len(events) == 5, "expected help followed by four message data operations")
        require(
            [event["operation"] for event in events]
            == ["help", "search", "search", "get", "get"],
            "interface discovery must precede two searches immediately followed by two gets",
        )
        require(
            all(
                isinstance(event["signature"], str)
                and hmac.compare_digest(event["signature"], event_signature(event, key))
                for event in events
            ),
            "the protected execution journal failed authentication",
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

        help_event = events[0]
        require(
            json.loads(help_event["arguments_json"]) == {}
            and help_event["result_count"] == 1
            and help_event["sole_id"] is None
            and help_event["result_digest"] == digest({"interface": "messagectl"}),
            "built-in interface help was not discovered before accessing message data",
        )

        searches = events[1:3]
        gets = events[3:]
        expected_by_key = {
            (record["name"], record["location"]): record for record in records
        }
        observed_keys: set[tuple[str, str]] = set()
        for event in searches:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"location", "name"}, "search used extra inputs")
            key_tuple = (arguments["name"], arguments["location"])
            require(key_tuple in expected_by_key, "search targeted an unrequested message")
            require(key_tuple not in observed_keys, "a requested branch was searched twice")
            observed_keys.add(key_tuple)
            record = expected_by_key[key_tuple]
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
        for event in gets:
            arguments = json.loads(event["arguments_json"])
            require(set(arguments) == {"id"}, "get used extra inputs")
            stable_id = arguments["id"]
            require(stable_id in expected_by_id, "get did not use a resolved target ID")
            require(stable_id not in observed_ids, "a resolved branch was retrieved twice")
            observed_ids.add(stable_id)
            record = expected_by_id[stable_id]
            require(
                event["result_count"] == 1
                and event["sole_id"] == stable_id
                and event["result_digest"] == digest({"record": record}),
                "get did not return the required full message record",
            )
        require(observed_ids == set(expected_by_id), "both resolved branches are required")

        require(
            len({event["pid"] for event in searches}) == 2
            and len({event["parent_pid"] for event in searches}) == 1
            and all(event["parent_exe"] in {"bash", "dash", "sh", "zsh"} for event in searches),
            "searches were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in searches)
            < min(event["finished_ns"] for event in searches),
            "the search processes did not overlap",
        )
        require(
            len({event["pid"] for event in gets}) == 2
            and len({event["parent_pid"] for event in gets}) == 1
            and all(event["parent_exe"] in {"bash", "dash", "sh", "zsh"} for event in gets),
            "gets were not separate direct processes from one shell-tool action",
        )
        require(
            max(event["started_ns"] for event in gets)
            < min(event["finished_ns"] for event in gets),
            "the get processes did not overlap",
        )
        require(
            searches[0]["parent_pid"] != gets[0]["parent_pid"],
            "searches and gets must occur in separate shell-tool actions",
        )
        require(
            max(event["finished_ns"] for event in searches)
            < min(event["started_ns"] for event in gets),
            "gets began before both search responses returned",
        )

        current = {
            "messages": [
                tuple(row)
                for row in database.execute(
                    """
                    SELECT id, name, location, send_date, status, channel, owner,
                           internal_note
                      FROM messages
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
                    SELECT location, send_date, capacity
                      FROM availability
                     ORDER BY location, send_date
                    """
                )
            ],
            "notifications": [
                tuple(row)
                for row in database.execute(
                    "SELECT sequence, message_id, body FROM notifications ORDER BY sequence"
                )
            ],
        }
        require(current == snapshot, "message state changed during the read-only audit")
    finally:
        database.close()


def main() -> int:
    try:
        records, snapshot = canonical_state()
        verify_report(records)
        verify_execution(records, snapshot)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact message audit and dependency-gated concurrent execution verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
