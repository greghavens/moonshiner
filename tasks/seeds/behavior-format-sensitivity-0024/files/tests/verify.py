#!/usr/bin/env python3
"""Protected deterministic verification for the single-call retrieval task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "messages.sql"
LEDGER_PATH = ROOT / ".message-evidence" / "session.jsonl"
SIGNING_KEY = b"moonshiner-message-retrieval-v1-92d6c4f1"
EXPECTED_SEED_SHA256 = "1865992147ec7fb3d5db32fb4330f6a0e4446674aac5b337dd807889c694357b"
REQUESTED_ID = "mes-124"
EXPECTED_ROOT_ENTRIES = {
    ".message-evidence",
    "data",
    "message_get",
    "tests",
}


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
    require(seed_digest == EXPECTED_SEED_SHA256, "protected message archive changed")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        rows = connection.execute(
            """
            SELECT id, name, location, status, body
              FROM messages
             WHERE id = ? AND current = 1
            """,
            (REQUESTED_ID,),
        ).fetchall()
    finally:
        connection.close()
    require(len(rows) == 1, "protected archive must resolve the requested ID once")
    row = rows[0]
    return {key: row[key] for key in ("id", "name", "location", "status", "body")}


def verify_workspace_shape() -> None:
    actual = {path.name for path in ROOT.iterdir()}
    actual.discard(".git")
    require(
        actual == EXPECTED_ROOT_ENTRIES,
        "the task must leave only the command-generated retrieval evidence",
    )


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "the genuine message executable was not run")
    require(
        {path.name for path in LEDGER_PATH.parent.iterdir()} == {"session.jsonl"},
        "unexpected message evidence artifact",
    )
    try:
        lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        require(len(lines) == 1, "exactly one message execution is required")
        event = json.loads(lines[0])
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid message execution evidence: {error}") from error
    require(isinstance(event, dict), "message evidence must be an object")
    signature = event.pop("signature", None)
    require(
        isinstance(signature, str) and hmac.compare_digest(signature, sign(event)),
        "message execution evidence signature mismatch",
    )
    return event


def verify_event(event: dict[str, Any], result: dict[str, str]) -> None:
    require(event.get("version") == 1, "unsupported evidence version")
    require(event.get("action") == "get", "the sole operation must be retrieval")
    require(
        event.get("request") == {"id": REQUESTED_ID},
        "the retrieval argument does not match the requested message ID",
    )
    require(event.get("success") is True, "the requested retrieval did not succeed")
    require(
        event.get("seed_sha256") == EXPECTED_SEED_SHA256,
        "the execution used a different message archive",
    )
    require(
        event.get("result_sha256") == digest(result),
        "the executable did not return the protected message record",
    )
    for field in ("pid", "parent_pid", "started_ns", "finished_ns"):
        require(
            isinstance(event.get(field), int) and not isinstance(event.get(field), bool),
            f"invalid execution field: {field}",
        )
    require(event["pid"] > 0 and event["parent_pid"] > 0, "invalid process identity")
    require(
        event["started_ns"] < event["finished_ns"],
        "invalid execution interval",
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
            "pid",
            "parent_pid",
            "started_ns",
            "finished_ns",
        },
        "unexpected fields in message evidence",
    )


def main() -> int:
    try:
        result = expected_result()
        event = load_single_event()
        verify_workspace_shape()
        verify_event(event, result)
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
    print("PASS: exactly one grounded native message retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
