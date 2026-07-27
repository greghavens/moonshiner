#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0133."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_RECORDS_PATH = ROOT / ".catalog" / "records.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_RECORDS_PATH = RUNTIME_DIR / "catalog-records.json"
INITIAL_RECORDS_PATH = ROOT / ".protected" / "initial_records.json"
AUDIT_PATH = RUNTIME_DIR / "catalog-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "catalog-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "catalog"
RECEIPT_PATH = ROOT / "cancellation_receipt.txt"

EXPECTED_EXECUTABLE_SHA256 = "36b1e00c9b572916f83ea6f7fe4e67df367f3ef278ba6816b39f21adbb151a22"
EXPECTED_INITIAL_RECORDS_SHA256 = "30c81070fd2609f2abc6adfff5d68e10340f77da0b3819a9c6813f20309ec46a"
EXPECTED_SEED_RECORDS_SHA256 = "30c81070fd2609f2abc6adfff5d68e10340f77da0b3819a9c6813f20309ec46a"
EXPECTED_REFERENCE_SETUP_SHA256 = "ccdd734d571e6ca3959eb11e7f32d8b599003add4ca6fcd3fa06857c29aa8cbf"
EXPECTED_AUDIT_KEY_SHA256 = "469ddab9e8c3dd7fce0f3f1be33fe7f5b6b94bb94940c72a1175ce64e110cfa7"
EXPECTED_GITIGNORE_SHA256 = "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
REFERENCE_MARKER_SHA256 = "970366d1af338aefbedf34a4beee9f6c2d8b2392bba9946fff6cc7bf02854517"

TARGET_ID = "lib-233"
OTHER_IDS = {"lib-133", "lib-633"}
TITLE = "River Almanac 133"
REASON = "superseded edition removed from circulation."
EXPECTED_RECEIPT = (
    "Title ID: lib-233\n"
    "Verified status: cancelled\n"
    "Title: River Almanac 133\n"
    "Cancellation reason: superseded edition removed from circulation.\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".catalog/records.json",
    ".protected/audit.key",
    ".protected/initial_records.json",
    ".protected/reference_setup.py",
    ".protected/runtime/catalog-audit.jsonl",
    ".protected/runtime/catalog-help.json",
    ".protected/runtime/catalog.lock",
    ".protected/runtime/catalog-records.json",
    ".protected/verify.py",
    ".reference_solution",
    "cancellation_receipt.txt",
    "catalog",
    ".gitignore",
}
ALLOWED_DIRECTORIES = {".catalog", ".protected", ".protected/runtime"}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected input {path.relative_to(ROOT)}: {error}")
    raise AssertionError("unreachable")


def require_digest(path: Path, expected: str) -> None:
    if not path.is_file() or digest(path) != expected:
        fail(f"protected input changed: {path.relative_to(ROOT)}")


def verify_workspace_shape() -> None:
    paths: list[Path] = []
    for top_level in ROOT.iterdir():
        if top_level.name == ".git":
            continue
        paths.append(top_level)
        if top_level.is_dir():
            paths.extend(top_level.rglob("*"))
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        allowed = relative in (
            ALLOWED_DIRECTORIES if path.is_dir() else ALLOWED_FILES
        )
        if not allowed:
            fail(f"unexpected workspace artifact: {relative}")
    marker = ROOT / ".reference_solution"
    if marker.exists() and (
        not marker.is_file() or digest(marker) != REFERENCE_MARKER_SHA256
    ):
        fail("invalid reference marker")


def load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot read {label}: {error}")
    if not isinstance(value, dict):
        fail(f"{label} is not a JSON object")
    return value


def validate_seal(value: dict[str, Any], label: str) -> dict[str, Any]:
    supplied = value.get("seal")
    if not isinstance(supplied, str):
        fail(f"{label} has no seal")
    unsigned = dict(value)
    del unsigned["seal"]
    try:
        key = KEY_PATH.read_bytes().strip()
    except OSError as error:
        fail(f"cannot read protected audit key: {error}")
    expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        fail(f"{label} was not emitted intact by catalog")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {"event": "catalog-help", "version": 1}:
        fail("catalog top-level built-in help was not used")


def record_map(
    document: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    records = document.get("records")
    if document.get("version") != 1 or not isinstance(records, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(record, dict)
        and isinstance(record.get("id"), str)
        and record["id"]
        for record in records
    ):
        fail(f"{label} contains an invalid catalog title")
    result = {record["id"]: record for record in records}
    if len(result) != len(records):
        fail(f"{label} contains a duplicate stable ID")
    return result


def load_audit() -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with AUDIT_PATH.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    fail(f"audit line {line_number} is invalid JSON: {error}")
                if not isinstance(event, dict):
                    fail(f"audit line {line_number} is not an object")
                events.append(validate_seal(event, f"audit event {line_number}"))
    except OSError as error:
        fail(f"cannot read execution journal: {error}")
    return events


def require_int(event: dict[str, Any], key: str, label: str) -> int:
    value = event.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        fail(f"{label} has invalid {key}")
    return value


def record_digest(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = record_map(
        load_json(INITIAL_RECORDS_PATH, "protected initial catalog"),
        "protected initial catalog",
    )
    current = record_map(
        load_json(CURRENT_RECORDS_PATH, "current catalog"),
        "current catalog",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial catalog has an unexpected title set")
    if (
        initial[TARGET_ID].get("title") != TITLE
        or initial[TARGET_ID].get("status") != "active"
        or initial[TARGET_ID].get("cancellation_reason") is not None
    ):
        fail("lib-233 is not the requested active catalog title")
    if set(current) != set(initial):
        fail("a catalog title was created or deleted")

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    revision = expected_target.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        fail("lib-233 has an invalid initial revision")
    expected_target["revision"] = revision + 1
    if current[TARGET_ID] != expected_target:
        fail("lib-233 was not cancelled exactly once for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another catalog title was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
) -> None:
    if len(events) != 3:
        fail(f"expected exactly three catalog events, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3]:
        fail("execution journal sequence is incomplete or reordered")
    if [event.get("operation") for event in events] != [
        "cancel",
        "transport-timeout",
        "get",
    ]:
        fail("required cancel-timeout-get recovery order was not followed")

    cancellation, timeout, retrieval = events
    if (
        cancellation.get("record_id") != TARGET_ID
        or cancellation.get("reason") != REASON
        or cancellation.get("before_status") != "active"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("updated") != 1
        or cancellation.get("outcome") != "committed"
    ):
        fail("the single cancellation request was incorrect or did not commit")
    cancel_started = require_int(cancellation, "started_ns", "cancellation event")
    committed = require_int(cancellation, "committed_ns", "cancellation event")
    if cancel_started >= committed:
        fail("cancellation execution interval is invalid")
    request_id = cancellation.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        fail("cancellation request has no stable request ID")

    if (
        timeout.get("request_operation") != "cancel"
        or timeout.get("request_id") != request_id
        or timeout.get("record_id") != TARGET_ID
        or timeout.get("signal") != "SIGTERM"
        or timeout.get("outcome") != "timeout"
    ):
        fail("the committed request was not ended by the required transport timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")

    if (
        retrieval.get("record_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("title") != TITLE
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("record_sha256") != record_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish the stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("lib-233 was not retrieved after the timeout completed")


def verify_receipt() -> None:
    if not RECEIPT_PATH.is_file():
        fail("cancellation_receipt.txt is missing")
    try:
        actual = RECEIPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cancellation_receipt.txt is not readable UTF-8: {error}")
    if actual != EXPECTED_RECEIPT:
        fail("cancellation_receipt.txt is not the exact grounded ordered receipt")


def main() -> None:
    verify_workspace_shape()
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256)
    require_digest(INITIAL_RECORDS_PATH, EXPECTED_INITIAL_RECORDS_SHA256)
    require_digest(SEED_RECORDS_PATH, EXPECTED_SEED_RECORDS_SHA256)
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target)
    verify_receipt()
    print(
        "verification passed: lib-233 was cancelled once, retrieved after the uncertain timeout, and reported in the required order without retry or creation"
    )


if __name__ == "__main__":
    main()
