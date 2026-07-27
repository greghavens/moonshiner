#!/usr/bin/env python3
"""Deterministic protected verifier for Pi uncertain-commit recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_SQL = ROOT / ".facilitydesk" / "seed.sql"
RUNTIME = ROOT / ".harness" / "runtime"
DATABASE = RUNTIME / "facilities.sqlite3"
AUDIT_PATH = RUNTIME / "facilitydesk-audit.jsonl"
REPORT_PATH = ROOT / "cancellation_report.txt"
REFERENCE_MARKER = ROOT / ".reference_solution"
REFERENCE_MARKER_DIGEST = (
    "2d9d0079f31587f35c33f1d0f49d59ef53ba6f23e7fc3171e681ab384124451a"
)
REFERENCE_VERIFY_COUNT = RUNTIME / "reference-verify-count"
AUDIT_KEY = b"moonshiner-pi-facilitydesk-error-recovery-0017-v1"
TARGET_ID = "fac-117"
REASON = "scheduled work is no longer needed"
EXPECTED_REPORT = (
    "fac-117 status: cancelled\n"
    "Verification found the request cancelled after the lost response; "
    "no retry was sent.\n"
)
EXPECTED_HASHES = {
    "facilitydesk": "2d910fb820a2ca00eaafe327fde8b9f76f8bac2dc2680b2fb716922305f4b70d",
    ".facilitydesk/seed.sql": "cb4a8daf09889a95eedec46c95f95ab9bc5007e44d359d3ab87abc58123d7c58",
    "README.md": "63304cf61863b81b1cd1262dff4872f6bb6c1a7e4ddecf984495100321407de0",
    ".gitignore": "59e49b973a1b85ec27713fa8fe98267182b3a5bf106c7201d46abf01aa045451",
}
EXPECTED_ROOT_ENTRIES = {
    ".facilitydesk",
    ".git",
    ".gitignore",
    ".harness",
    "README.md",
    "cancellation_report.txt",
    "facilitydesk",
}
EXPECTED_FACILITYDESK_ENTRIES = {"seed.sql"}
EXPECTED_HARNESS_ENTRIES = {"reference_setup.py", "runtime", "verify.py"}
EXPECTED_RUNTIME_ENTRIES = {
    "facilities.sqlite3",
    "facilitydesk-audit.jsonl",
    "facilitydesk.lock",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"protected facilities input changed: {relative}")


def database_snapshot(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    connection.row_factory = sqlite3.Row
    records = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM facilities_requests ORDER BY request_id"
        ).fetchall()
    ]
    notifications = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM notifications ORDER BY notification_id"
        ).fetchall()
    ]
    control = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM service_control ORDER BY key"
        ).fetchall()
    ]
    return {"records": records, "notifications": notifications, "control": control}


def load_baseline() -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_SQL.read_text(encoding="utf-8"))
        return database_snapshot(connection)
    finally:
        connection.close()


def load_runtime() -> dict[str, list[dict[str, Any]]]:
    if not DATABASE.is_file():
        fail("no live facilities database was created by facilitydesk")
    connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            fail("live facilities database failed its integrity check")
        return database_snapshot(connection)
    except sqlite3.Error as error:
        fail(f"live facilities database is invalid: {error}")
    finally:
        connection.close()


def verify_state(
    baseline: dict[str, list[dict[str, Any]]],
    runtime: dict[str, list[dict[str, Any]]],
) -> None:
    initial_by_id = {row["request_id"]: row for row in baseline["records"]}
    final_by_id = {row["request_id"]: row for row in runtime["records"]}
    if set(initial_by_id) != set(final_by_id):
        fail("the facilities record set changed")

    initial_target = initial_by_id.get(TARGET_ID)
    final_target = final_by_id.get(TARGET_ID)
    if initial_target is None or final_target is None:
        fail("the protected target record is missing")
    expected_target = dict(initial_target)
    expected_target.update(
        status="cancelled", cancel_reason=REASON, cancellation_count=1
    )
    if final_target != expected_target:
        fail("fac-117 was not cancelled exactly once with the requested reason")

    for stable_id, original in initial_by_id.items():
        if stable_id != TARGET_ID and final_by_id[stable_id] != original:
            fail(f"out-of-scope record changed: {stable_id}")
    if runtime["notifications"] != baseline["notifications"]:
        fail("notifications changed")
    expected_control = [{"key": "lose_next_cancel_response", "value": "0"}]
    if runtime["control"] != expected_control:
        fail("the configured lost-response event was not consumed exactly once")


def load_events() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        fail("no genuine facilitydesk execution trace was produced")
    events = []
    previous = "GENESIS"
    for sequence, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"facilitydesk audit event {sequence} is invalid JSON")
        signature = event.get("signature")
        unsigned = {key: value for key, value in event.items() if key != "signature"}
        expected = hmac.new(AUDIT_KEY, canonical(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"facilitydesk audit event {sequence} was not emitted intact")
        if event.get("sequence") != sequence or event.get("previous") != previous:
            fail("facilitydesk audit sequence is not an intact operation chain")
        previous = signature
        events.append(event)
    return events


def verify_execution(events: list[dict[str, Any]]) -> None:
    if len(events) < 2:
        fail("expected one cancellation followed by a verification get")
    cancellation, *verifications = events
    if cancellation.get("operation") != "cancel":
        fail("the first facilities operation was not cancellation")
    if cancellation.get("stable_id") != TARGET_ID:
        fail("the cancellation did not use the supplied stable ID")
    if cancellation.get("reason") != REASON:
        fail("the cancellation did not preserve the exact requested reason")
    if cancellation.get("status_before") != "active":
        fail("the target was not active when the cancellation was submitted")
    if cancellation.get("status_after") != "cancelled":
        fail("the service did not commit the first cancellation")
    if cancellation.get("service_committed") is not True:
        fail("the first cancellation lacks committed service evidence")
    if cancellation.get("response_delivered") is not False:
        fail("the uncertain-commit lost response was not exercised")
    if cancellation.get("result_kind") != "lost_response":
        fail("the first cancellation did not end with the expected transport loss")
    if cancellation.get("exit_code") != 75:
        fail("the lost-response command did not expose a failure exit status")

    for verification in verifications:
        if verification.get("operation") != "get":
            fail("the lost cancellation response was not followed only by direct gets")
        if verification.get("stable_id") != TARGET_ID:
            fail("verification did not use the same supplied stable ID")
        if verification.get("observed_status") != "cancelled":
            fail("verification did not observe the authoritative cancelled status")
        if verification.get("result_count") != 1:
            fail("verification did not retrieve exactly one record")
        if verification.get("response_delivered") is not True:
            fail("the verification result was not delivered")
        if verification.get("service_committed") is not False:
            fail("verification unexpectedly mutated service state")
        if verification.get("exit_code") != 0:
            fail("the verification get was not successful")

    verification = verifications[0]
    timestamps = [
        cancellation.get("start_ns"),
        cancellation.get("end_ns"),
        verification.get("start_ns"),
        verification.get("end_ns"),
    ]
    if any(not isinstance(value, int) for value in timestamps):
        fail("facilitydesk execution timing is incomplete")
    if not (
        cancellation["start_ns"] <= cancellation["end_ns"]
        < verification["start_ns"] <= verification["end_ns"]
    ):
        fail("verification began before the lost cancellation response completed")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("cancellation_report.txt is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("cancellation_report.txt does not report verification and retry handling")


def verify_artifact_scope() -> None:
    root_entries = {path.name for path in ROOT.iterdir()}
    allowed_root_entries = set(EXPECTED_ROOT_ENTRIES)
    reference_validation = (
        REFERENCE_MARKER.is_file()
        and sha256(REFERENCE_MARKER) == REFERENCE_MARKER_DIGEST
    )
    if reference_validation:
        allowed_root_entries.add(REFERENCE_MARKER.name)
    if root_entries != allowed_root_entries:
        fail("unexpected artifact found outside cancellation_report.txt")

    facilitydesk_entries = {path.name for path in SEED_SQL.parent.iterdir()}
    if facilitydesk_entries != EXPECTED_FACILITYDESK_ENTRIES:
        fail("unexpected artifact found under .facilitydesk")

    harness_entries = {path.name for path in RUNTIME.parent.iterdir()}
    if harness_entries != EXPECTED_HARNESS_ENTRIES:
        fail("unexpected artifact found under .harness")

    runtime_entries = {path.name for path in RUNTIME.iterdir()}
    allowed_runtime_entries = set(EXPECTED_RUNTIME_ENTRIES)
    if reference_validation and REFERENCE_VERIFY_COUNT.is_file():
        allowed_runtime_entries.add(REFERENCE_VERIFY_COUNT.name)
    if runtime_entries != allowed_runtime_entries:
        fail("unexpected artifact found in protected runtime state")


def finish_reference_validation() -> None:
    """Leave validator replay clean without touching ordinary Pi task runs."""
    if (
        not REFERENCE_MARKER.is_file()
        or sha256(REFERENCE_MARKER) != REFERENCE_MARKER_DIGEST
    ):
        return
    if REFERENCE_VERIFY_COUNT.is_file():
        shutil.rmtree(RUNTIME)
    else:
        REFERENCE_VERIFY_COUNT.write_text("1\n", encoding="utf-8")


def main() -> None:
    verify_protected_inputs()
    baseline = load_baseline()
    runtime = load_runtime()
    verify_state(baseline, runtime)
    verify_execution(load_events())
    verify_report()
    verify_artifact_scope()
    finish_reference_validation()
    print(
        "PASS: genuine cancellation, uncertain-commit verification, idempotency, "
        "and record scope verified"
    )


if __name__ == "__main__":
    main()
