#!/usr/bin/env python3
"""Protected deterministic verification for one native subscription retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "subscription_get"
SEED_PATH = ROOT / "data" / "subscriptions.sql"
LEDGER_PATH = ROOT / ".subscription-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-subscription-get-v1-3c6c9e2a"
EXPECTED_EXECUTABLE_SHA256 = "05f754e55b523ac844ff16aa638e616e02ce0840abce3e43107218244aa830df"
EXPECTED_SEED_SHA256 = "2e26d3342e6dd61a2a0fabc000c1053617e6e4c2bac29de37c8d4fa4689749c1"
REQUESTED_ID = "tel-138"


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
        "supplied subscription executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected subscription archive changed",
    )


def expected_result() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT
                id,
                account_name,
                plan,
                status,
                activated_at,
                next_billing_date,
                monthly_price_cents,
                line_count
              FROM subscriptions
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
        "account_name": row["account_name"],
        "plan": row["plan"],
        "status": row["status"],
        "activated_at": row["activated_at"],
        "next_billing_date": row["next_billing_date"],
        "monthly_price_cents": row["monthly_price_cents"],
        "line_count": row["line_count"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine subscription executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected subscription evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one subscription execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid subscription execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "subscription evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "subscription execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "get", "the sole operation must retrieve a subscription")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal subscription ID",
    )
    require(event.get("success") is True, "the requested subscription retrieval failed")
    require(event.get("sequence") == 1, "the retrieval was not the first operation")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different subscription archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected subscription result",
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
            "sequence",
        },
        "unexpected fields in subscription evidence",
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
    print("PASS: exactly one grounded native subscription retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
