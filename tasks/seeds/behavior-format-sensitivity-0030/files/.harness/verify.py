#!/usr/bin/env python3
"""Deterministic protected verification for the project-card retrieval."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT / "data" / "projects.sql"
DB_PATH = ROOT / ".project-runtime" / "projects.sqlite3"
LEDGER_PATH = ROOT / ".project-runtime" / "operations.jsonl"
DELIVERABLE = ROOT / "task_card.json"
SIGNING_KEY = b"moonshiner-projectdesk-format-v1-a9c42f7e"
EXPECTED_SEED_SHA256 = "9054fad82b49c8002e9535579c55b44e51009c7b9cf80bc14e75e71919d29d3f"
REQUESTED_ID = "pro-130"
DISTRACTOR_ID = "pro-530"
EXPECTED_DIRECTORIES = {
    ".harness",
    ".project-runtime",
    "data",
}
EXPECTED_FILES = {
    ".gitignore",
    ".harness/verify.py",
    ".project-runtime/initialize.lock",
    ".project-runtime/operations.jsonl",
    ".project-runtime/projects.sqlite3",
    "README.md",
    "data/projects.sql",
    "projectdesk",
    "task_card.json",
}
EXPECTED_STATIC_SHA256 = {
    ".gitignore": "d68bf1d7b6d511e886cc5c435fd3ec91f5ac6714dca37dda085654dbc71967d0",
    "README.md": "df1c7f77e0ba5284ca2c2d615307229a683640bb3697548a7ab155aa5111aaf6",
    "data/projects.sql": EXPECTED_SEED_SHA256,
    "projectdesk": "85713e2da10a7c8ddd4cb041c1ca49543c7b4de37ebd10a469436d223640a11e",
}


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def signature(event: dict[str, Any]) -> str:
    payload = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    digest = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    require(digest == EXPECTED_SEED_SHA256, "protected project data was modified")
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
    return connection


def complete_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["stable_id"],
        "type": row["record_type"],
        "title": row["title"],
        "project": row["project"],
        "status": row["status"],
        "assignee": row["assignee"],
        "details": row["details"],
    }


def verify_read_only(canonical: sqlite3.Connection) -> None:
    require(DB_PATH.is_file(), "the genuine projectdesk executable was not run")
    actual = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        require(
            list(actual.iterdump()) == list(canonical.iterdump()),
            "project registry schema or data changed",
        )
    finally:
        actual.close()


def expected_result(canonical: sqlite3.Connection) -> dict[str, Any]:
    row = canonical.execute(
        "SELECT * FROM records WHERE stable_id = ?",
        (REQUESTED_ID,),
    ).fetchone()
    require(row is not None, "protected requested record is absent")
    require(row["record_type"] == "task_card", "requested record is not a task card")
    distractor = canonical.execute(
        "SELECT record_type FROM records WHERE stable_id = ?",
        (DISTRACTOR_ID,),
    ).fetchone()
    require(
        distractor is not None and distractor["record_type"] == "project_note",
        "protected distractor is not a project note",
    )
    return {"record": complete_record(row)}


def load_single_event() -> dict[str, Any]:
    require(LEDGER_PATH.is_file(), "missing command-generated project evidence")
    try:
        raw = LEDGER_PATH.read_text(encoding="utf-8")
        require(
            raw.endswith("\n") and raw.count("\n") == 1,
            "exactly one project-registry data operation is required",
        )
        signed = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid project evidence: {error}") from error
    require(isinstance(signed, dict), "project evidence must be an object")
    require(
        raw == json.dumps(signed, sort_keys=True, separators=(",", ":")) + "\n",
        "project evidence is not in the client's native format",
    )
    supplied_signature = signed.pop("signature", None)
    require(
        isinstance(supplied_signature, str)
        and hmac.compare_digest(supplied_signature, signature(signed)),
        "project evidence signature mismatch",
    )
    return signed


def verify_event(event: dict[str, Any], expected: dict[str, Any]) -> None:
    require(event.get("version") == 1, "unsupported project evidence version")
    require(event.get("success") is True, "the project retrieval did not succeed")
    require(event.get("action") == "get", "the sole operation was not a direct get")
    require(
        event.get("request") == {"id": REQUESTED_ID},
        "the direct get did not use only the requested string ID",
    )
    require(
        event.get("result") == expected,
        "the recorded native result does not match the protected task card",
    )


def verify_deliverable(expected: dict[str, Any]) -> None:
    require(DELIVERABLE.is_file(), "missing task_card.json")
    wanted = json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise VerificationError(f"cannot read task_card.json: {error}") from error
    require(actual == wanted, "task_card.json is not the exact native stdout")
    require(
        len(actual.splitlines()) == 1,
        "task_card.json must contain exactly one JSON line",
    )


def verify_workspace() -> None:
    expected_paths = EXPECTED_DIRECTORIES | EXPECTED_FILES
    actual_paths = {
        relative.as_posix()
        for path in ROOT.rglob("*")
        if (relative := path.relative_to(ROOT)).parts[0] != ".git"
    }
    require(
        actual_paths == expected_paths,
        "files outside the permitted deliverable and registry runtime were changed",
    )
    for relative in EXPECTED_DIRECTORIES:
        path = ROOT / relative
        require(
            path.is_dir() and not path.is_symlink(),
            f"expected directory was replaced: {relative}",
        )
    for relative in EXPECTED_FILES:
        path = ROOT / relative
        require(
            path.is_file() and not path.is_symlink(),
            f"expected file was replaced: {relative}",
        )
    for relative, wanted in EXPECTED_STATIC_SHA256.items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        require(actual == wanted, f"protected file was modified: {relative}")
    require(
        (ROOT / ".project-runtime" / "initialize.lock").read_bytes() == b"",
        "project runtime lock was modified",
    )


def main() -> int:
    try:
        canonical = canonical_connection()
        try:
            expected = expected_result(canonical)
            verify_read_only(canonical)
            verify_event(load_single_event(), expected)
            verify_deliverable(expected)
            verify_workspace()
        finally:
            canonical.close()
    except (
        VerificationError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        sqlite3.Error,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: one exact direct project-card retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
