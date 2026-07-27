#!/usr/bin/env python3
"""Protected deterministic verifier for staged CRM cancellation behavior."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_DB = ROOT / ".crm-seed" / "accounts.sqlite3"
RUNTIME_DB = ROOT / ".crm-runtime" / "accounts.sqlite3"
AUDIT_PATH = ROOT / ".crm-runtime" / "audit.jsonl"
REPORT_PATH = ROOT / "cancellation-result.json"
SIGNING_KEY = b"moonshiner-missing-function-crm-0009-v1"
TARGET_ID = "crm-109"
TARGET_NAME = "Meadowlark Foods"
TARGET_REASON = "the account was imported twice"
LIMITED = "limited"
READY = "cancellation-ready"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def signature(record: dict[str, Any]) -> str:
    return hmac.new(
        SIGNING_KEY,
        canonical_json(record).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def rows(path: Path) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT stable_id, name, region, status, cancellation_reason "
                "FROM accounts ORDER BY stable_id"
            ).fetchall()
        ]
    finally:
        connection.close()


def load_events() -> list[dict[str, Any]]:
    require(AUDIT_PATH.is_file(), "missing command-generated CRM audit evidence")
    try:
        events = [
            json.loads(line)
            for line in AUDIT_PATH.read_text(encoding="utf-8").splitlines()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid CRM audit evidence: {error}") from error
    require(len(events) == 4, "expected exactly four CRM interactions")
    for index, signed_event in enumerate(events):
        require(isinstance(signed_event, dict), "each CRM audit event must be an object")
        event = dict(signed_event)
        observed_signature = event.pop("signature", None)
        require(
            isinstance(observed_signature, str)
            and hmac.compare_digest(observed_signature, signature(event)),
            "CRM audit signature mismatch",
        )
        require(event.get("version") == 1, "unsupported CRM audit version")
        require(event.get("sequence") == index, "CRM audit sequence is not contiguous")
        require(event.get("exit_code") == 0, "a CRM interaction failed")
        for field in ("pid", "parent_pid", "started_ns", "finished_ns"):
            require(isinstance(event.get(field), int), f"invalid audit field {field}")
        require(
            isinstance(event.get("interaction_id"), str)
            and bool(event["interaction_id"]),
            "invalid CRM interaction identity",
        )
        require(event["started_ns"] < event["finished_ns"], "invalid audit interval")
        events[index] = event
    return events


def verify_interactions(events: list[dict[str, Any]]) -> None:
    require(
        [event.get("operation") for event in events]
        == ["help", "refresh", "help", "cancel"],
        "CRM interactions did not follow missing-function discovery and continuation order",
    )
    require(
        len({event["interaction_id"] for event in events}) == 4,
        "each CRM interaction must come from a separate shell interaction",
    )

    first_help, refresh, second_help, cancellation = events
    require(
        first_help.get("phase_before") == LIMITED
        and first_help.get("phase_after") == LIMITED,
        "initial help was not observed in the limited capability phase",
    )
    first_operations = first_help.get("result", {}).get("available_operations")
    require(
        isinstance(first_operations, list)
        and "cancel" not in first_operations
        and "refresh" in first_operations,
        "initial help did not demonstrate the missing cancellation operation",
    )
    require(
        refresh.get("phase_before") == LIMITED
        and refresh.get("phase_after") == READY
        and refresh.get("request") == {},
        "the genuine registry refresh did not expose the later capability",
    )
    require(
        second_help.get("phase_before") == READY
        and second_help.get("phase_after") == READY,
        "help was not inspected after the capability changed",
    )
    second_operations = second_help.get("result", {}).get("available_operations")
    require(
        isinstance(second_operations, list) and "cancel" in second_operations,
        "later help did not expose the cancellation operation",
    )
    require(
        cancellation.get("phase_before") == READY
        and cancellation.get("phase_after") == READY,
        "cancellation ran before the operation was genuinely available",
    )
    require(
        cancellation.get("request")
        == {"id": TARGET_ID, "reason": TARGET_REASON},
        "cancellation did not use the supplied stable ID and exact reason",
    )
    expected_result = {
        "id": TARGET_ID,
        "name": TARGET_NAME,
        "reason": TARGET_REASON,
        "status": "cancelled",
    }
    require(
        cancellation.get("result") == expected_result,
        "cancellation result does not match the CRM service result",
    )


def verify_database() -> None:
    require(RUNTIME_DB.is_file(), "missing runtime CRM database")
    before = rows(SEED_DB)
    after = rows(RUNTIME_DB)
    require(len(before) == len(after), "CRM record count changed")
    before_by_id = {record["stable_id"]: record for record in before}
    after_by_id = {record["stable_id"]: record for record in after}
    require(before_by_id.keys() == after_by_id.keys(), "CRM record identities changed")
    require(TARGET_ID in before_by_id, "protected target record is missing")

    for stable_id, original in before_by_id.items():
        current = after_by_id[stable_id]
        if stable_id == TARGET_ID:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = TARGET_REASON
            require(current == expected, "target record was not cancelled exactly")
        else:
            require(
                current == original,
                f"non-target CRM record {stable_id} was modified",
            )

    connection = sqlite3.connect(f"file:{RUNTIME_DB}?mode=ro", uri=True)
    try:
        cancellations = connection.execute(
            "SELECT stable_id, reason FROM cancellations ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()
    require(
        cancellations == [(TARGET_ID, TARGET_REASON)],
        "cancellation history must contain exactly the requested action",
    )


def verify_report() -> None:
    require(REPORT_PATH.is_file(), "missing cancellation-result.json")
    expected_result = {
        "id": TARGET_ID,
        "name": TARGET_NAME,
        "reason": TARGET_REASON,
        "status": "cancelled",
    }
    expected = canonical_json(expected_result) + "\n"
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except OSError as error:
        raise VerificationError(f"cannot read cancellation result: {error}") from error
    require(
        actual == expected,
        "cancellation-result.json is not the verbatim successful command output",
    )


def main() -> int:
    try:
        verify_report()
        events = load_events()
        verify_interactions(events)
        verify_database()
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: staged genuine CRM cancellation verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
