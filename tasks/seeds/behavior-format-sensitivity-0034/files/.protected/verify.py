#!/usr/bin/env python3
"""Protected deterministic verification for one native expense open."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "expense_open"
SEED_PATH = ROOT / "data" / "expenses.sql"
LEDGER_PATH = ROOT / ".expense-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-expense-open-v1-c8e390b1"
EXPECTED_EXECUTABLE_SHA256 = "f718042aa60aa461c42bec6aa53a37e200f73d95f8d405ffff8a62fce362c903"
EXPECTED_SEED_SHA256 = "af014120478d6864f6b9fe3b9e0b2ae87fb61c7e1a99fc3bc40c95f22a9d906c"
REQUESTED_ID = "exp-134"


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
        "supplied expense executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected expense archive changed",
    )


def expected_result() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, merchant, expense_date, amount_cents,
                   currency, category, status
              FROM expenses
             WHERE id = ?
            """,
            (REQUESTED_ID,),
        ).fetchall()
    finally:
        connection.close()
    require(len(rows) == 1, "protected archive must resolve the requested ID once")
    row = rows[0]
    return {
        "id": row["id"],
        "merchant": row["merchant"],
        "expense_date": row["expense_date"],
        "amount_cents": row["amount_cents"],
        "currency": row["currency"],
        "category": row["category"],
        "status": row["status"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine expense executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected expense evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one expense execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid expense execution evidence: {error}") from error
    require(isinstance(event, dict), "expense evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "expense execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "open", "the sole operation must open an expense")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal expense ID",
    )
    require(event.get("success") is True, "the requested expense open failed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different expense archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected expense result",
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
        "unexpected fields in expense evidence",
    )


def main() -> int:
    try:
        verify_supplied_files()
        verify_event(load_single_event(), expected_result())
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
    print("PASS: exactly one grounded native expense open verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
