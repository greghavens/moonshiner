#!/usr/bin/env python3
"""Protected deterministic verification for one native work-order open."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "work_order_open"
SEED_PATH = ROOT / "data" / "work_orders.sql"
LEDGER_PATH = ROOT / ".work-order-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-work-order-open-v1-2f719ac4"
EXPECTED_EXECUTABLE_SHA256 = "8c2f03006be27c7b5d10bc18fbf21c0ad45039ca4ddd5b128755f902677430bd"
EXPECTED_SEED_SHA256 = "2f80df190ca14f0bf91297e42936fdc0865b956177188af87c04ef247530f1e8"
REQUESTED_ID = "fac-137"


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
        "supplied work-order executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected work-order archive changed",
    )


def expected_result() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, site, summary, priority, assigned_team, opened_at, status
              FROM work_orders
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
        "site": row["site"],
        "summary": row["summary"],
        "priority": row["priority"],
        "assigned_team": row["assigned_team"],
        "opened_at": row["opened_at"],
        "status": row["status"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine work-order executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected work-order evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one work-order execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid work-order execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "work-order evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "work-order execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "open", "the sole operation must open a work order")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal work-order ID",
    )
    require(event.get("success") is True, "the requested work-order open failed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different work-order archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected work-order result",
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
        "unexpected fields in work-order evidence",
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
    print("PASS: exactly one grounded native work-order open verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
