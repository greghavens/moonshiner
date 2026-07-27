#!/usr/bin/env python3
"""Protected deterministic verification for one native candidate open."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "recruiting_open"
SEED_PATH = ROOT / "data" / "candidates.sql"
LEDGER_PATH = ROOT / ".recruiting-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-recruiting-open-v1-27bd5c91"
EXPECTED_EXECUTABLE_SHA256 = "24bb8f8534c7668c2397f95b91415767e5cddd1dcce3b94ef33dbe86fddf202e"
EXPECTED_SEED_SHA256 = "efa40b7afd9ec2b42874d01912d508377daae7e9eb5a8ca658d8681edce2bc68"
REQUESTED_ID = "rec-131"


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
        "supplied recruiting executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected recruiting archive changed",
    )


def expected_result() -> dict[str, str]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, full_name, role, stage, recruiter
              FROM candidates
             WHERE id = ? AND active = 1
            """,
            (REQUESTED_ID,),
        ).fetchall()
    finally:
        connection.close()
    require(len(rows) == 1, "protected archive must resolve the requested ID once")
    row = rows[0]
    return {
        key: row[key]
        for key in ("id", "full_name", "role", "stage", "recruiter")
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine recruiting executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected recruiting evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one recruiting execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid recruiting execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "recruiting evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "recruiting execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, str]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "open", "the sole operation must open a candidate")
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the open argument is not the requested literal candidate ID",
    )
    require(event.get("success") is True, "the requested candidate open failed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different recruiting archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected candidate record",
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
        "unexpected fields in recruiting evidence",
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
    print("PASS: exactly one grounded native candidate open verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
