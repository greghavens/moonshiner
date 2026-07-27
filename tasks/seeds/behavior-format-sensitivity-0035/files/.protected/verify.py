#!/usr/bin/env python3
"""Protected deterministic verification for one native appointment pull."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "appointment_pull"
SEED_PATH = ROOT / "data" / "appointments.sql"
LEDGER_PATH = ROOT / ".appointment-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-appointment-pull-v1-9a34dfe2"
EXPECTED_EXECUTABLE_SHA256 = "4606634bccd18eedb7a09337f2ca0a8d3b1ee69a81480999249f7e88c2c6d5b7"
EXPECTED_SEED_SHA256 = "9e6097353ef2a2e93a4935585ca28d2f625fdc0224ae655071832b57a9079180"
REQUESTED_ID = "hea-135"


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
        "supplied appointment executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected appointment archive changed",
    )


def expected_result() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, patient_name, starts_at, timezone,
                   clinician, visit_type, status
              FROM appointments
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
        "patient_name": row["patient_name"],
        "starts_at": row["starts_at"],
        "timezone": row["timezone"],
        "clinician": row["clinician"],
        "visit_type": row["visit_type"],
        "status": row["status"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine appointment executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected appointment evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one appointment execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid appointment execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "appointment evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "appointment execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "pull", "the sole operation must pull an appointment")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal appointment ID",
    )
    require(event.get("success") is True, "the requested appointment pull failed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different appointment archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected appointment result",
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
        "unexpected fields in appointment evidence",
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
    print("PASS: exactly one grounded native appointment pull verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
