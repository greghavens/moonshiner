#!/usr/bin/env python3
"""Protected verification for one grounded native vehicle-file retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "vehicle_get"
SEED_PATH = ROOT / "data" / "vehicles.sql"
LEDGER_PATH = ROOT / ".vehicle-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-vehicle-get-0040-v1-8f2671c3"
EXPECTED_EXECUTABLE_SHA256 = "c8807b546da1a21d0c4efaee33f57483f12bf5e9811946cb6ae314e99384bf24"
EXPECTED_SEED_SHA256 = "a760a955e4e2db4b5be2dbb69ab18d2c5076f01eb647895021ba9c1c01769c50"
REQUESTED_ID = "fle-140"


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
        "supplied vehicle executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected fleet archive changed",
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
                name,
                vehicle_type,
                location,
                status,
                service_date,
                odometer_miles,
                assigned_route
              FROM vehicles
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
        "name": row["name"],
        "vehicle_type": row["vehicle_type"],
        "location": row["location"],
        "status": row["status"],
        "service_date": row["service_date"],
        "odometer_miles": row["odometer_miles"],
        "assigned_route": row["assigned_route"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine vehicle executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected vehicle evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one vehicle execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid vehicle execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "vehicle evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "vehicle execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "get", "the sole operation must retrieve a vehicle")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal vehicle ID",
    )
    require(event.get("success") is True, "the requested vehicle retrieval failed")
    require(event.get("sequence") == 1, "the retrieval was not the first operation")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different fleet archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected vehicle file",
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
        "unexpected fields in vehicle evidence",
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
    print("PASS: exactly one grounded native vehicle retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
