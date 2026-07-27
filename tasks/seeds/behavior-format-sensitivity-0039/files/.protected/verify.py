#!/usr/bin/env python3
"""Protected deterministic verification for one native application retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "application_get"
SEED_PATH = ROOT / "data" / "applications.sql"
LEDGER_PATH = ROOT / ".application-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-application-get-v1-82d71fc4"
EXPECTED_EXECUTABLE_SHA256 = "267a38687222fa8a4dfd33ce5f730c64ae4fa42cfe7a0e95f9fbd038a037f148"
EXPECTED_SEED_SHA256 = "3e2c49a1269f13f2e72ad06a1badd5f1a45cb38527ae594adb86ac26b18ab1be"
REQUESTED_ID = "pub-139"


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
        "supplied application executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected application archive changed",
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
                location,
                status
              FROM applications
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
        "location": row["location"],
        "status": row["status"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine application executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected application evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one application execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid application execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "application evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "application execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "get", "the sole operation must retrieve an application")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal application ID",
    )
    require(event.get("success") is True, "the requested application retrieval failed")
    require(event.get("sequence") == 1, "the retrieval was not the first operation")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different application archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected application result",
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
        "unexpected fields in application evidence",
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
    print("PASS: exactly one grounded native application retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
