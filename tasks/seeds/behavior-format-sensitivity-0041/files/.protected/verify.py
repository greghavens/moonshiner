#!/usr/bin/env python3
"""Protected verification for one grounded native calendar retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXECUTABLE_PATH = ROOT / "calendar_open"
SEED_PATH = ROOT / "data" / "calendar_archive.sql"
LEDGER_PATH = ROOT / ".calendar-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-calendar-open-0041-v1-4d9c7a21"
EXPECTED_EXECUTABLE_SHA256 = "a52167e57722832dfce34578381858ced076a7215bf054f243f5e70275982907"
EXPECTED_SEED_SHA256 = "da60eaeeee1f953c62d6ad82058cf7014559be35bc9e78cea216871271bc5d6d"
REQUESTED_ID = "cal-141"


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
        "supplied calendar executable changed",
    )
    require(
        hashlib.sha256(SEED_PATH.read_bytes()).hexdigest() == EXPECTED_SEED_SHA256,
        "protected calendar archive changed",
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
                title,
                location,
                starts_at,
                ends_at,
                organizer,
                status,
                notes
              FROM calendar_entries
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
        "title": row["title"],
        "location": row["location"],
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "organizer": row["organizer"],
        "status": row["status"],
        "notes": row["notes"],
    }


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine calendar executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected calendar evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one calendar execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(
            f"invalid calendar execution evidence: {error}"
        ) from error
    require(isinstance(event, dict), "calendar evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "calendar execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(
        event.get("action") == "open",
        "the sole operation must open a calendar entry",
    )
    request = event.get("request")
    require(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request == {"id": REQUESTED_ID},
        "the lookup argument is not the requested literal calendar ID",
    )
    require(event.get("success") is True, "the requested calendar retrieval failed")
    require(event.get("sequence") == 1, "the retrieval was not the first operation")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different calendar archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the complete protected calendar entry",
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
        "unexpected fields in calendar evidence",
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
    print("PASS: exactly one grounded native calendar retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
