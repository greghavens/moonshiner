#!/usr/bin/env python3
"""Protected deterministic verifier for the Tripdesk workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = ROOT / ".protected" / "trips.sql"
KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_PATH = ROOT / ".tripdesk-runtime" / "audit.jsonl"

REQUESTED_SCOPE = {
    "city": "Kyoto",
    "name": "Kyoto Textile Study",
    "status": "active",
}
EXPECTED_SUPPLIED_SHA256 = {
    ".gitignore": "4c40240b9c1c6318bc95155f169bda19b87a3f91fb5a76affcf293f2d77f4093",
    ".protected/audit.key": "080b5bc94eb89b7f42b0fac3f6705a671efff962553e366989a0478f7591f3fb",
    ".protected/trips.sql": "4c4742c608be202f13e8988d4dbb8d1db5e8a19626d07d020624031893fcca80",
    "README.md": "2eb9e7ac9aa85ac7e0f7acc2e8188f4c1aec955bbd24c264c67ab76e922e16c3",
    "long_trip_handoff.md": "e5feddc8937acb3bd3d9c85ae8822bc9c93b75396dd41e569b66d333a40a75c1",
    "tripdesk": "b38d7428833d416f9a279a5d4a712b8d3b13cd40a6bf07f8e0f4946e189a78b5",
}
ALLOWED_RELATIVE_FILES = {
    ".gitignore",
    ".protected/audit.key",
    ".protected/trips.sql",
    ".protected/verify.py",
    ".tripdesk-runtime/audit.jsonl",
    "README.md",
    "long_trip_handoff.md",
    "tripdesk",
}
ALLOWED_RELATIVE_DIRECTORIES = {
    ".protected",
    ".tripdesk-runtime",
}
SUMMARY_FIELDS = (
    "id",
    "name",
    "city",
    "country",
    "status",
    "start_date",
    "end_date",
)
DETAIL_FIELDS = (
    "id",
    "name",
    "city",
    "country",
    "status",
    "start_date",
    "end_date",
    "coordinator",
    "venue",
    "focus",
    "participant_count",
    "notes",
)


class VerificationError(RuntimeError):
    pass


def compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def fail(message: str) -> None:
    raise VerificationError(message)


def verify_supplied_state() -> None:
    for relative_path, expected_sha256 in EXPECTED_SUPPLIED_SHA256.items():
        path = ROOT / relative_path
        try:
            actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            fail(f"supplied file is missing: {relative_path}")
        if actual_sha256 != expected_sha256:
            fail(f"supplied file changed: {relative_path}")
    if ROOT.joinpath("tripdesk").stat().st_mode & 0o111 == 0:
        fail("Tripdesk is no longer executable")


def load_events() -> list[dict[str, Any]]:
    try:
        raw_lines = AUDIT_PATH.read_bytes().splitlines()
    except FileNotFoundError:
        fail("no Tripdesk execution evidence was found")
    if len(raw_lines) != 2 or any(not line for line in raw_lines):
        fail("expected exactly two archive data operations")
    events: list[dict[str, Any]] = []
    for line in raw_lines:
        try:
            value = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("execution evidence is not valid JSONL")
        if not isinstance(value, dict):
            fail("execution evidence must contain JSON objects")
        events.append(value)
    return events


def verify_event_signatures(events: list[dict[str, Any]]) -> None:
    key = KEY_PATH.read_bytes().strip()
    seed_sha256 = hashlib.sha256(SEED_PATH.read_bytes()).hexdigest()
    expected_keys = {
        "action",
        "request",
        "result_sha256",
        "seed_sha256",
        "sequence",
        "signature",
        "success",
        "version",
    }
    for index, event in enumerate(events, start=1):
        if set(event) != expected_keys:
            fail("execution evidence has an unexpected shape")
        if event["sequence"] != index or event["version"] != 1:
            fail("execution evidence sequence is invalid")
        if event["seed_sha256"] != seed_sha256:
            fail("the trip archive seed changed")
        signature = event["signature"]
        if not isinstance(signature, str):
            fail("execution evidence signature is invalid")
        unsigned = dict(event)
        del unsigned["signature"]
        expected_signature = hmac.new(
            key,
            compact_json(unsigned),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            fail("execution evidence signature does not verify")


def expected_results() -> tuple[dict[str, Any], dict[str, Any]]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript(SEED_PATH.read_text(encoding="utf-8"))
        record_count = connection.execute(
            "SELECT COUNT(*) FROM trips"
        ).fetchone()[0]
        if record_count < 50:
            fail("protected fixture is not distractor-heavy")
        same_name_elsewhere = connection.execute(
            """
            SELECT COUNT(*)
              FROM trips
             WHERE name = ? COLLATE BINARY
               AND city != ? COLLATE BINARY
               AND status = ? COLLATE BINARY
            """,
            (
                REQUESTED_SCOPE["name"],
                REQUESTED_SCOPE["city"],
                REQUESTED_SCOPE["status"],
            ),
        ).fetchone()[0]
        if same_name_elsewhere < 1:
            fail("same-name location distractors are missing")
        near_name_in_city = connection.execute(
            """
            SELECT COUNT(*)
              FROM trips
             WHERE city = ? COLLATE BINARY
               AND status = ? COLLATE BINARY
               AND name != ? COLLATE BINARY
               AND (
                    name LIKE 'Kyoto Textile %'
                    OR name LIKE '%Textile%Kyoto%'
               )
            """,
            (
                REQUESTED_SCOPE["city"],
                REQUESTED_SCOPE["status"],
                REQUESTED_SCOPE["name"],
            ),
        ).fetchone()[0]
        if near_name_in_city < 1:
            fail("near-name Kyoto distractors are missing")
        same_name_other_status = connection.execute(
            """
            SELECT COUNT(*)
              FROM trips
             WHERE name = ? COLLATE BINARY
               AND city = ? COLLATE BINARY
               AND status != ? COLLATE BINARY
            """,
            (
                REQUESTED_SCOPE["name"],
                REQUESTED_SCOPE["city"],
                REQUESTED_SCOPE["status"],
            ),
        ).fetchone()[0]
        if same_name_other_status < 1:
            fail("same-name status distractors are missing")
        rows = connection.execute(
            """
            SELECT id, name, city, country, status, start_date, end_date
              FROM trips
             WHERE name = ? COLLATE BINARY
               AND city = ? COLLATE BINARY
               AND status = ? COLLATE BINARY
             ORDER BY id
            """,
            (
                REQUESTED_SCOPE["name"],
                REQUESTED_SCOPE["city"],
                REQUESTED_SCOPE["status"],
            ),
        ).fetchall()
        if len(rows) != 1:
            fail("protected fixture does not resolve to one trip")
        search_result = {
            "count": 1,
            "matches": [
                {field: rows[0][field] for field in SUMMARY_FIELDS}
            ],
            "scope": dict(REQUESTED_SCOPE),
        }
        returned_id = rows[0]["id"]
        detail_row = connection.execute(
            """
            SELECT
                id, name, city, country, status, start_date, end_date,
                coordinator, venue, focus, participant_count, notes
              FROM trips
             WHERE id = ? COLLATE BINARY
            """,
            (returned_id,),
        ).fetchone()
        if detail_row is None:
            fail("resolved trip is absent from protected fixture")
        detail_result = {
            field: detail_row[field] for field in DETAIL_FIELDS
        }
        detail_result["record_type"] = "trip"
        return search_result, detail_result
    finally:
        connection.close()


def result_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(compact_json(value) + b"\n").hexdigest()


def verify_workflow(events: list[dict[str, Any]]) -> None:
    search_event, get_event = events
    if (
        search_event["action"] != "search"
        or search_event["request"] != REQUESTED_SCOPE
        or search_event["success"] is not True
    ):
        fail("first data operation was not the required scoped search")

    search_result, detail_result = expected_results()
    returned_id = search_result["matches"][0]["id"]
    if search_event["result_sha256"] != result_hash(search_result):
        fail("scoped search did not return the protected expected result")
    if (
        get_event["action"] != "get"
        or get_event["request"] != {"id": returned_id}
        or get_event["success"] is not True
    ):
        fail("second data operation did not retrieve the returned stable ID")
    if get_event["result_sha256"] != result_hash(detail_result):
        fail("full-detail retrieval output did not match the resolved trip")


def verify_runtime_shape() -> None:
    try:
        entries = sorted(
            path.name for path in (ROOT / ".tripdesk-runtime").iterdir()
        )
    except FileNotFoundError:
        fail("Tripdesk runtime directory is missing")
    if entries != ["audit.jsonl"]:
        fail("unexpected Tripdesk runtime artifacts were created")


def verify_no_extra_paths() -> None:
    ignored_roots = {".agents", ".codex", ".git"}
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in ignored_roots:
            continue
        rendered = relative.as_posix()
        if path.is_symlink() or path.is_file():
            if rendered not in ALLOWED_RELATIVE_FILES:
                fail(f"unexpected persistent file was created: {rendered}")
            continue
        if path.is_dir() and rendered not in ALLOWED_RELATIVE_DIRECTORIES:
            fail(f"unexpected persistent directory was created: {rendered}")
        if not path.is_dir():
            fail(f"unexpected persistent file was created: {rendered}")


def main() -> int:
    try:
        verify_supplied_state()
        events = load_events()
        verify_event_signatures(events)
        verify_workflow(events)
        verify_runtime_shape()
        verify_no_extra_paths()
    except (OSError, sqlite3.Error, VerificationError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: scoped search and dependent full-detail retrieval verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
