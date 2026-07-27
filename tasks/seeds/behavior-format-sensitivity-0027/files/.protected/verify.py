#!/usr/bin/env python3
"""Protected deterministic verification for the single inventory open."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "inventory_open"
SEED_PATH = ROOT / "data" / "inventory.sql"
LEDGER_PATH = ROOT / ".inventory-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-inventory-open-v1-81c734fa"
EXPECTED_EXECUTABLE_SHA256 = "f036c77a8e440eed8d2025bc82c32e78ba7b076bff6b44005856e3576db8657d"
EXPECTED_SEED_SHA256 = "497f6411513fbb83ca80587e320a5fda610755d36ebbd6fdc83f021d7de0646f"
REQUESTED_ID = "inv-127"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(compact_json(value).encode("utf-8")).hexdigest()


def sign(record: dict[str, Any]) -> str:
    return hmac.new(
        SIGNING_KEY,
        compact_json(record).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_supplied_files() -> None:
    require(
        hashlib.sha256(EXECUTABLE_PATH.read_bytes()).hexdigest()
        == EXPECTED_EXECUTABLE_SHA256,
        "supplied inventory executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected inventory archive changed",
    )


def expected_result() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, item_name, facility, audit_status, quantity
              FROM inventory_items
             WHERE id = ? AND current = 1
            """,
            (REQUESTED_ID,),
        ).fetchall()
    finally:
        connection.close()
    require(len(rows) == 1, "protected archive must resolve the audit target once")
    row = rows[0]
    return {
        key: row[key]
        for key in ("id", "item_name", "facility", "audit_status", "quantity")
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine inventory executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected inventory evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one inventory execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid inventory execution evidence: {error}") from error
    require(isinstance(event, dict), "inventory evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "inventory execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "open", "the sole operation must open one item")
    require(
        event.get("request") == {"id": REQUESTED_ID},
        "the open argument does not match the requested inventory ID",
    )
    require(event.get("success") is True, "opening the audit target did not succeed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different inventory archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the protected inventory record",
    )
    require(
        set(event)
        == {
            "version",
            "action",
            "request",
            "success",
            "result_sha256",
            "seed_sha256",
        },
        "unexpected fields in inventory evidence",
    )


def main() -> int:
    try:
        verify_supplied_files()
        result = expected_result()
        verify_event(load_single_event(), result)
    except (
        VerificationError,
        KeyError,
        TypeError,
        OSError,
        UnicodeError,
        sqlite3.Error,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exactly one grounded native inventory open verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
