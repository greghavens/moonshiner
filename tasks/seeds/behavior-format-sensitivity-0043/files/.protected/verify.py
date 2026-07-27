#!/usr/bin/env python3
"""Protected verifier for the one-call concierge retrieval task."""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
DATABASE = PROTECTED / "bookings.sqlite3"
EVIDENCE_KEY = PROTECTED / "evidence.key"
JOURNAL = ROOT / ".runtime" / "calls.jsonl"
RESPONSE = ROOT / "response.txt"


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def expected_record() -> bytes:
    uri = DATABASE.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                """
                SELECT id, name, location, status
                FROM reservations
                WHERE id = ?
                """,
                ("hos-143",),
            ).fetchone()
    except sqlite3.Error as error:
        fail(f"protected reservation store could not be read: {error}")
    if row is None:
        fail("protected target reservation is absent")
    stream = io.StringIO(newline="")
    csv.writer(stream, lineterminator="\n").writerow(
        tuple(str(value) for value in row)
    )
    return stream.getvalue().encode("utf-8")


def read_single_event() -> dict[str, object]:
    if not JOURNAL.is_file():
        fail("no concierge invocation was recorded")
    raw = JOURNAL.read_bytes()
    if len(raw) > 16_384:
        fail("invocation journal is unexpectedly large")
    lines = raw.splitlines()
    if len(lines) != 1:
        fail(f"expected exactly one concierge invocation, observed {len(lines)}")
    try:
        event = json.loads(lines[0])
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("invocation evidence is malformed")
    if not isinstance(event, dict):
        fail("invocation evidence is not an object")
    return event


def authenticate(event: dict[str, object]) -> dict[str, object]:
    expected_fields = {
        "argv",
        "exit_code",
        "operation",
        "record_id",
        "sig",
        "stdout_sha256",
        "v",
    }
    if set(event) != expected_fields:
        fail("invocation evidence has unexpected fields")
    signature = event.get("sig")
    if not isinstance(signature, str):
        fail("invocation signature is malformed")
    payload = {key: value for key, value in event.items() if key != "sig"}
    canonical = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    wanted = hmac.new(
        EVIDENCE_KEY.read_bytes(), canonical, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, wanted):
        fail("invocation signature is invalid")
    return payload


def main() -> int:
    payload = authenticate(read_single_event())
    if payload["v"] != 1:
        fail("unsupported invocation evidence version")
    if payload["argv"] != ["reservation", "--id", "hos-143"]:
        fail("the single invocation did not request reservation hos-143 directly")
    if payload["operation"] != "reservation" or payload["record_id"] != "hos-143":
        fail("the recorded operation or reservation ID is wrong")
    if payload["exit_code"] != 0:
        fail("the concierge invocation did not succeed")

    if not RESPONSE.is_file():
        fail("response.txt is absent")
    observed = RESPONSE.read_bytes()
    wanted = expected_record()
    if observed != wanted:
        fail("response.txt is not the byte-exact interface record")
    if payload["stdout_sha256"] != hashlib.sha256(observed).hexdigest():
        fail("response.txt does not match the recorded invocation stdout")

    print("verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
