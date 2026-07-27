#!/usr/bin/env python3
"""Protected deterministic verification for one native shipment retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "shipments.sql"
LEDGER_PATH = ROOT / ".shipping-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-shipping-retrieval-v1-8b629d4c"
EXPECTED_SEED_SHA256 = "0eb08f9559e7107cf151aaae7cf1fe387cff2cbf36c05f50c15ffd481acb3a9d"
REQUESTED_ID = "shi-128"


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


def expected_result() -> dict[str, str]:
    seed_digest = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    require(seed_digest == EXPECTED_SEED_SHA256, "protected shipment archive changed")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, name, location, status
              FROM shipments
             WHERE id = ? AND current = 1
            """,
            (REQUESTED_ID,),
        ).fetchall()
    finally:
        connection.close()
    require(len(rows) == 1, "protected archive must resolve the requested ID once")
    row = rows[0]
    return {
        key: row[key]
        for key in ("id", "name", "location", "status")
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine shipping executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected shipping evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one shipping execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid shipping execution evidence: {error}") from error
    require(isinstance(event, dict), "shipping evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "shipping execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, str]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "get", "the sole operation must be retrieval")
    require(
        event.get("request") == {"id": REQUESTED_ID},
        "the retrieval argument does not match the requested shipment ID",
    )
    require(event.get("success") is True, "the requested retrieval did not succeed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different shipment archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the protected shipment record",
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
        "unexpected fields in shipping evidence",
    )


def main() -> int:
    try:
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
    print("PASS: exactly one grounded native shipment retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
