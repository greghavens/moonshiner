#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0115."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_APPOINTMENTS_PATH = ROOT / ".clinic" / "appointments.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_APPOINTMENTS_PATH = RUNTIME_DIR / "appointments.json"
INITIAL_APPOINTMENTS_PATH = ROOT / ".protected" / "initial_appointments.json"
AUDIT_PATH = RUNTIME_DIR / "appointment-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "appointment-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "appointmentctl"
RECEIPT_PATH = ROOT / "cancellation_receipt.txt"

EXPECTED_EXECUTABLE_SHA256 = "5b769d8b0d434d28e2cae5cab093c681bfc10d7fe2c1aa0e8564de2c0431c9f5"
EXPECTED_INITIAL_APPOINTMENTS_SHA256 = "e06959f4971d3585110204651bf760c4a9c3030332e3f3a567bdebee3ef5a8a7"
EXPECTED_SEED_APPOINTMENTS_SHA256 = "e06959f4971d3585110204651bf760c4a9c3030332e3f3a567bdebee3ef5a8a7"
EXPECTED_REFERENCE_SETUP_SHA256 = "bae10d11226533690fa2bdb517abc8529441298459f382d2820560c103ace71c"
EXPECTED_AUDIT_KEY_SHA256 = "b2562d73468ae892218193d094b2271dfee9986e91b6875f173cea8f3b6438b5"
EXPECTED_GITIGNORE_SHA256 = "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
EXPECTED_CLINIC_DOC_SHA256 = "a1c59f0f627c555da176ed56d4d97274d1409ca3d0b8c6fc45833b8679e1ff60"
REFERENCE_MARKER_SHA256 = "c0dc45e38de8f3f4e8c343c6a95d97c38abd5fcc031429db47a8e66dc86a6a0c"

TARGET_ID = "hea-215"
OTHER_IDS = {"hea-615", "hea-815"}
NAME = "Dental cleaning 115"
REASON = "patient requested cancellation"
EXPECTED_RECEIPT = (
    "Appointment: Dental cleaning 115 (hea-215)\n"
    "Status: cancelled\n"
    "Reason: patient requested cancellation\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".clinic/appointments.json",
    ".gitignore",
    ".protected/audit.key",
    ".protected/initial_appointments.json",
    ".protected/reference_setup.py",
    ".protected/runtime/appointment-audit.jsonl",
    ".protected/runtime/appointment-help.json",
    ".protected/runtime/appointment.lock",
    ".protected/runtime/appointments.json",
    ".protected/verify.py",
    ".reference_solution",
    "CLINIC.md",
    "appointmentctl",
    "cancellation_receipt.txt",
}
ALLOWED_DIRECTORIES = {".clinic", ".protected", ".protected/runtime"}


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
        fail(f"{label} was not emitted intact by appointmentctl")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {"event": "appointmentctl-help", "version": 1}:
        fail("appointmentctl built-in help was not used")


def appointment_map(
    document: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    appointments = document.get("appointments")
    if document.get("version") != 1 or not isinstance(appointments, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(appointment, dict)
        and isinstance(appointment.get("id"), str)
        and appointment["id"]
        for appointment in appointments
    ):
        fail(f"{label} contains an invalid appointment")
    result = {
        appointment["id"]: appointment
        for appointment in appointments
    }
    if len(result) != len(appointments):
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
                events.append(
                    validate_seal(event, f"audit event {line_number}")
                )
    except OSError as error:
        fail(f"cannot read execution journal: {error}")
    return events


def require_int(event: dict[str, Any], key: str, label: str) -> int:
    value = event.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        fail(f"{label} has invalid {key}")
    return value


def appointment_digest(appointment: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(appointment)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = appointment_map(
        load_json(INITIAL_APPOINTMENTS_PATH, "protected initial registry"),
        "protected initial registry",
    )
    current = appointment_map(
        load_json(CURRENT_APPOINTMENTS_PATH, "current registry"),
        "current registry",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial registry has an unexpected appointment set")
    if (
        initial[TARGET_ID].get("name") != NAME
        or initial[TARGET_ID].get("status") != "active"
    ):
        fail("hea-215 is not the requested active appointment")
    if set(current) != set(initial):
        fail("an appointment was created or deleted")

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    if current[TARGET_ID] != expected_target:
        fail("hea-215 was not cancelled exactly for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another appointment was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
) -> None:
    if len(events) != 3:
        fail(f"expected exactly three appointment events, found {len(events)}")
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
        cancellation.get("appointment_id") != TARGET_ID
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
        or timeout.get("appointment_id") != TARGET_ID
        or timeout.get("signal") != "SIGTERM"
        or timeout.get("outcome") != "timeout"
    ):
        fail("the committed request was not ended by the required transport timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")

    if (
        retrieval.get("appointment_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("name") != NAME
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("record_sha256")
        != appointment_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish the stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("hea-215 was not retrieved after the timeout completed")


def verify_receipt() -> None:
    if not RECEIPT_PATH.is_file():
        fail("cancellation_receipt.txt is missing")
    try:
        actual = RECEIPT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cancellation_receipt.txt is not readable UTF-8: {error}")
    if actual != EXPECTED_RECEIPT:
        fail("cancellation_receipt.txt is not the exact grounded receipt")


def main() -> None:
    verify_workspace_shape()
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256)
    require_digest(
        INITIAL_APPOINTMENTS_PATH,
        EXPECTED_INITIAL_APPOINTMENTS_SHA256,
    )
    require_digest(SEED_APPOINTMENTS_PATH, EXPECTED_SEED_APPOINTMENTS_SHA256)
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    require_digest(ROOT / "CLINIC.md", EXPECTED_CLINIC_DOC_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("appointmentctl is not executable")
    verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target)
    verify_receipt()
    print(
        "verification passed: one timed-out cancellation committed, "
        "hea-215 was then retrieved as cancelled, and no retry or creation occurred"
    )


if __name__ == "__main__":
    main()
