#!/usr/bin/env python3
"""Protected verification for behavior-multi-turn-state-0196."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "claims.sqlite3"
INITIAL_CLAIMS = ROOT / ".protected" / "initial_claims.json"
INITIAL_NOTIFICATIONS = ROOT / ".protected" / "initial_notifications.json"
KEY = ROOT / ".protected" / "audit.key"
CLAIMS_EXECUTABLE = ROOT / "claims"
CLI_DOCUMENTATION = ROOT / "CLAIMS_CLI.md"
COMPARISON = ROOT / "comparison.md"
FINAL_ANSWER = ROOT / "final-answer.md"
TARGET_ID = "CLM-6648"
COMPARISON_ID = "CLM-9648"
FIRST_REQUESTED_DATE = "2026-11-19"
CORRECTED_DATE = "2026-12-23"
EXPECTED_CLAIMS_SHA256 = "d7eecdf190bf5373a98f2857c078dcdbb163446e85f5b8eecf556b85fd56ec30"
EXPECTED_DOCUMENTATION_SHA256 = "703601e256e5736eca4579bde92756f57734b5002825d6254d0b5170174b5508"
EXPECTED_INITIAL_CLAIMS_SHA256 = "96000aa83a74249c66427cb2ac4faf3f4c8cc375995daad514f22cf3c5222e57"
EXPECTED_INITIAL_NOTIFICATIONS_SHA256 = "8dfc43620bb58a73dffb21e561f082d2cb8c2c85ba5ad8ea3e82cd31bc5bc747"
EXPECTED_KEY_SHA256 = "ee288a2deee7ec27133b7fa640343d537b686f1796b541226c6193cfb4e7b5ea"
CLAIM_FIELDS = (
    "id",
    "name",
    "office",
    "review_date",
    "status",
    "canceled",
)


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def require_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected {label}: {error}")
    if actual != expected:
        fail(f"protected {label} was modified")


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")


def claim_map(records: Any, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        fail(f"{label} is not a list")
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        if (
            not isinstance(record, dict)
            or set(record) != set(CLAIM_FIELDS)
            or not isinstance(record.get("id"), str)
            or not record["id"]
            or not isinstance(record.get("canceled"), bool)
        ):
            fail(f"{label} contains an invalid claim")
        if record["id"] in result:
            fail(f"{label} contains a duplicate claim ID")
        result[record["id"]] = record
    return result


def sqlite_claims(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    try:
        rows = connection.execute(
            "SELECT id,name,office,review_date,status,canceled "
            "FROM claims ORDER BY id"
        ).fetchall()
    except sqlite3.Error as error:
        fail(f"cannot read current claims: {error}")
    records = []
    for row in rows:
        record = dict(zip(CLAIM_FIELDS, row, strict=True))
        if record["canceled"] not in (0, 1):
            fail("current claims contain an invalid canceled flag")
        record["canceled"] = bool(record["canceled"])
        records.append(record)
    return claim_map(records, "current claims")


require_digest(CLAIMS_EXECUTABLE, EXPECTED_CLAIMS_SHA256, "claims executable")
require_digest(
    CLI_DOCUMENTATION,
    EXPECTED_DOCUMENTATION_SHA256,
    "claims CLI documentation",
)
require_digest(
    INITIAL_CLAIMS,
    EXPECTED_INITIAL_CLAIMS_SHA256,
    "initial claim snapshot",
)
require_digest(
    INITIAL_NOTIFICATIONS,
    EXPECTED_INITIAL_NOTIFICATIONS_SHA256,
    "initial notification snapshot",
)
require_digest(KEY, EXPECTED_KEY_SHA256, "audit key")

initial_document = load_json(INITIAL_CLAIMS)
if not isinstance(initial_document, dict) or set(initial_document) != {"claims"}:
    fail("protected initial claim snapshot has an invalid shape")
initial = claim_map(initial_document["claims"], "protected initial claims")
if TARGET_ID not in initial or COMPARISON_ID not in initial:
    fail("protected initial claim snapshot is missing a requested claim")

try:
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
except sqlite3.Error as error:
    fail(f"cannot open claims database read-only: {error}")

with connection:
    current = sqlite_claims(connection)
    try:
        notification_rows = connection.execute(
            "SELECT id,claim_id,message FROM notifications ORDER BY id"
        ).fetchall()
        audit_rows = connection.execute(
            "SELECT sequence,payload,seal FROM audit ORDER BY sequence"
        ).fetchall()
    except sqlite3.Error as error:
        fail(f"cannot read notification or audit state: {error}")

if set(current) != set(initial):
    fail("claims were created or deleted")
for claim_id, original in initial.items():
    if claim_id == TARGET_ID:
        expected = dict(original)
        expected["review_date"] = CORRECTED_DATE
        if current[claim_id] != expected:
            fail("target claim does not contain only the corrected review date")
    elif current[claim_id] != original:
        fail(f"untargeted claim {claim_id} was modified")

initial_notification_document = load_json(INITIAL_NOTIFICATIONS)
if initial_notification_document != {"notifications": []}:
    fail("protected initial notification snapshot is invalid")
if notification_rows:
    fail("a notification was created")

if len(audit_rows) != 4:
    fail(f"expected exactly four executable claims operations, found {len(audit_rows)}")
events: list[dict[str, Any]] = []
key = KEY.read_bytes().strip()
for expected_sequence, (sequence, payload_text, seal) in enumerate(audit_rows, 1):
    if sequence != expected_sequence:
        fail("claims audit sequence is incomplete or reordered")
    try:
        payload = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError) as error:
        fail(f"claims audit payload is invalid: {error}")
    if not isinstance(payload, dict) or payload.get("sequence") != sequence:
        fail("claims audit payload has the wrong sequence")
    expected_seal = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    if not isinstance(seal, str) or not hmac.compare_digest(seal, expected_seal):
        fail("claims audit seal is invalid")
    started = payload.get("started_ns")
    finished = payload.get("finished_ns")
    if (
        not isinstance(payload.get("pid"), int)
        or payload["pid"] <= 1
        or not isinstance(started, int)
        or not isinstance(finished, int)
        or started >= finished
    ):
        fail("claims audit does not record a genuine process execution interval")
    events.append(payload)

if [event.get("operation") for event in events] != [
    "get",
    "get",
    "set-review-date",
    "set-review-date",
]:
    fail("claims operations were not exactly two retrievals followed by two updates")

retrieved: dict[str, dict[str, Any]] = {}
for event in events[:2]:
    claim_id = event.get("id")
    record = event.get("record")
    if (
        claim_id not in {TARGET_ID, COMPARISON_ID}
        or event.get("found") is not True
        or record != initial.get(claim_id)
        or claim_id in retrieved
    ):
        fail("initial executable retrievals did not return both requested full claims")
    retrieved[claim_id] = record
if set(retrieved) != {TARGET_ID, COMPARISON_ID}:
    fail("one of the two requested claims was not retrieved")

expected_transitions = [
    (initial[TARGET_ID]["review_date"], FIRST_REQUESTED_DATE),
    (FIRST_REQUESTED_DATE, CORRECTED_DATE),
]
for event, (before_date, after_date) in zip(
    events[2:], expected_transitions, strict=True
):
    before = event.get("before")
    after = event.get("after")
    expected_before = dict(initial[TARGET_ID])
    expected_before["review_date"] = before_date
    expected_after = dict(initial[TARGET_ID])
    expected_after["review_date"] = after_date
    if (
        event.get("id") != TARGET_ID
        or event.get("date") != after_date
        or event.get("found") is not True
        or before != expected_before
        or after != expected_after
    ):
        fail("review-date updates did not preserve target continuity and correction order")

expected_comparison = (
    f"{TARGET_ID} | {retrieved[TARGET_ID]['name']} | "
    f"{retrieved[TARGET_ID]['review_date']} | {retrieved[TARGET_ID]['status']}\n"
    f"{COMPARISON_ID} | {retrieved[COMPARISON_ID]['name']} | "
    f"{retrieved[COMPARISON_ID]['review_date']} | "
    f"{retrieved[COMPARISON_ID]['status']}\n"
)
try:
    actual_comparison = COMPARISON.read_text(encoding="utf-8")
except OSError as error:
    fail(f"cannot read comparison.md: {error}")
if actual_comparison != expected_comparison:
    fail("comparison.md does not exactly report both initially retrieved claims")

expected_final_answer = (
    f"- Target ID: {TARGET_ID}\n"
    f"- Corrected review date: {CORRECTED_DATE}\n"
    "- Notification sent: no\n"
)
try:
    actual_final_answer = FINAL_ANSWER.read_text(encoding="utf-8")
except OSError as error:
    fail(f"cannot read final-answer.md: {error}")
if actual_final_answer != expected_final_answer:
    fail("final-answer.md is not exactly the required three bullets")

print("PASS: multi-turn claim correction, negative constraints, and exact artifacts verified")
