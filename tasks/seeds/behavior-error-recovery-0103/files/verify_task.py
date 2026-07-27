#!/usr/bin/env python3
"""Protected acceptance verifier for the cancellation recovery task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
BASELINE_DATABASE = ROOT / "reservations.db"
RUNTIME_DATABASE = ROOT / ".reservation-runtime" / "reservations.db"
AUDIT_LOG = ROOT / ".reservation-runtime" / "operations.jsonl"
RESULT = ROOT / "cancellation_result.json"
TARGET_ID = "hos-203"
DISTRACTOR_ID = "hos-230"
EXPECTED_NAME = "Maple Salon seminar"
EXPECTED_REASON = "no longer needed for the scheduled work."
EXPECTED_BASELINE_FINGERPRINT = "cfb1d1e70168d9c940d3bc64aaca1efd38379cd3ccf393ad83540c6635ea0ed8"
EXPECTED_RESULT_KEYS = {"id", "name", "status", "reason"}
ALLOWED_FILES = {
    ".gitignore",
    ".reference_solution",
    "cancellation_result.json",
    "reference_setup.py",
    "reservationctl",
    "reservations.db",
    "verify_task.py",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def database_rows(path: Path) -> list[dict]:
    if not path.is_file():
        fail(f"database is missing: {path.name}")
    uri = f"file:{path}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT id, name, status, cancellation_reason, cancellation_requests,
                       scheduled_for, room
                  FROM reservations
                 ORDER BY id
                """
            ).fetchall()
    except sqlite3.Error as error:
        fail(f"invalid reservation database: {error}")
    return [dict(row) for row in rows]


def fingerprint(rows: list[dict]) -> str:
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_events() -> list[dict]:
    if not AUDIT_LOG.is_file():
        fail("no reservationctl operations were recorded")
    try:
        events = [json.loads(line) for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines()
                  if line]
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid operation audit: {error}")
    if not all(isinstance(event, dict) for event in events):
        fail("operation audit entries must be JSON objects")
    return events


def verify_workspace_shape() -> None:
    unexpected: list[str] = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if any(part in {".git", ".reservation-runtime", "__pycache__"}
               for part in relative.parts):
            continue
        if path.is_file() and relative.as_posix() not in ALLOWED_FILES \
                and path.suffix != ".pyc":
            unexpected.append(relative.as_posix())
    if unexpected:
        fail("unexpected deliverable files: " + ", ".join(sorted(unexpected)))


def verify_state(baseline: list[dict], runtime: list[dict]) -> dict:
    baseline_by_id = {row["id"]: row for row in baseline}
    runtime_by_id = {row["id"]: row for row in runtime}
    if set(runtime_by_id) != set(baseline_by_id):
        fail("reservation IDs changed")
    for record_id, row in baseline_by_id.items():
        if record_id == TARGET_ID:
            continue
        if runtime_by_id[record_id] != row:
            fail(f"non-target reservation was touched: {record_id}")
    if runtime_by_id[DISTRACTOR_ID] != baseline_by_id[DISTRACTOR_ID]:
        fail("the similarly named distractor was touched")
    target = runtime_by_id.get(TARGET_ID)
    if target is None:
        fail("target reservation is missing")
    if target["name"] != EXPECTED_NAME:
        fail("target reservation name changed")
    if target["status"] != "cancellation-pending":
        fail("target does not have the committed cancellation status")
    if target["cancellation_reason"] != EXPECTED_REASON:
        fail("target cancellation reason is incorrect")
    if target["cancellation_requests"] != 1:
        fail("cancellation mutation was not made exactly once")
    return target


def verify_events(events: list[dict]) -> None:
    expected_types = ["cancel_start", "cancel_commit", "client_timeout", "get"]
    if [event.get("type") for event in events] != expected_types:
        fail("required sequence is one cancellation, its real timeout, then one exact-ID get")
    if [event.get("seq") for event in events] != [1, 2, 3, 4]:
        fail("operation audit sequence is incomplete")
    if any(event.get("id") != TARGET_ID for event in events):
        fail("an operation addressed a reservation other than the supplied exact ID")
    request_ids = [event.get("request_id") for event in events[:3]]
    if not request_ids[0] or len(set(request_ids)) != 1:
        fail("timeout is not linked to the committed cancellation request")
    started, committed, timed_out, retrieved = events
    if started.get("reason") != EXPECTED_REASON or started.get("observed_status") != "active":
        fail("the cancellation start did not use the requested reason on an active record")
    if committed.get("previous_status") != "active" \
            or committed.get("status") != "cancellation-pending" \
            or committed.get("reason") != EXPECTED_REASON \
            or committed.get("cancellation_requests") != 1:
        fail("the durable cancellation commit is incorrect")
    if timed_out.get("id") != TARGET_ID:
        fail("client timeout is not for the target cancellation")
    if retrieved.get("found") is not True \
            or retrieved.get("name") != EXPECTED_NAME \
            or retrieved.get("status") != "cancellation-pending" \
            or retrieved.get("reason") != EXPECTED_REASON:
        fail("the exact-ID verification get did not ground the final result")


def verify_result(target: dict) -> None:
    if not RESULT.is_file():
        fail("cancellation_result.json is missing")
    try:
        result = json.loads(RESULT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"invalid cancellation_result.json: {error}")
    if not isinstance(result, dict) or set(result) != EXPECTED_RESULT_KEYS:
        fail("cancellation_result.json must contain exactly id, name, status, and reason")
    expected = {
        "id": target["id"],
        "name": target["name"],
        "status": target["status"],
        "reason": target["cancellation_reason"],
    }
    if result != expected:
        fail("cancellation_result.json is not grounded in the retrieved target state")


def main() -> int:
    verify_workspace_shape()
    baseline = database_rows(BASELINE_DATABASE)
    if fingerprint(baseline) != EXPECTED_BASELINE_FINGERPRINT:
        fail("the protected initial reservation database changed")
    runtime = database_rows(RUNTIME_DATABASE)
    target = verify_state(baseline, runtime)
    verify_events(load_events())
    verify_result(target)
    print("PASS: cancellation uncertainty was resolved by exact-ID retrieval without a retry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
