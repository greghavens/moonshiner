#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0135."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_APPOINTMENTS_PATH = ROOT / ".appointments" / "appointments.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_APPOINTMENTS_PATH = RUNTIME_DIR / "appointment-records.json"
INITIAL_APPOINTMENTS_PATH = ROOT / ".protected" / "initial_appointments.json"
AUDIT_PATH = RUNTIME_DIR / "appointment-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "appointment-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "appointmentctl"
RESULT_PATH = ROOT / "cancellation_result.txt"

EXPECTED_EXECUTABLE_SHA256 = (
    "464b3ff9d25028e3dedea9b35e2cf190f1c3bd90e6f1d0a6576e609b451b57a1"
)
EXPECTED_INITIAL_APPOINTMENTS_SHA256 = (
    "4edca03167ae816c5f8b6e84e8287edc58e569e38580aaeaaa1766f30080c335"
)
EXPECTED_SEED_APPOINTMENTS_SHA256 = (
    "4edca03167ae816c5f8b6e84e8287edc58e569e38580aaeaaa1766f30080c335"
)
EXPECTED_REFERENCE_SETUP_SHA256 = (
    "f1b89cb731d0e896a14a5f59d18c78770ed637f32b04017031cc319f385245c0"
)
EXPECTED_AUDIT_KEY_SHA256 = (
    "15ce3df6f4e5f19b4e04e891e150b212c37b58bbf33f7deb1d8e57d33a7c1eea"
)
EXPECTED_GITIGNORE_SHA256 = (
    "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
)

TARGET_ID = "hea-235"
OTHER_IDS = {"hea-135", "hea-635"}
APPOINTMENT_TYPE = "Dental cleaning"
REASON = "patient no longer needs the appointment"
EXPECTED_RESULT = (
    "Appointment ID: hea-235\n"
    "Verified status: cancelled\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".gitignore",
    ".appointments/appointments.json",
    ".protected/audit.key",
    ".protected/initial_appointments.json",
    ".protected/reference_setup.py",
    ".protected/runtime/appointment-audit.jsonl",
    ".protected/runtime/appointment-help.json",
    ".protected/runtime/appointment-records.json",
    ".protected/runtime/appointment.lock",
    ".protected/verify.py",
    "appointmentctl",
    "cancellation_result.txt",
}
ALLOWED_DIRECTORIES = {
    ".appointments",
    ".protected",
    ".protected/runtime",
}


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
    if not path.is_file() or path.is_symlink() or digest(path) != expected:
        fail(f"protected input changed: {path.relative_to(ROOT)}")


def verify_workspace_shape() -> None:
    paths: list[Path] = []
    for top_level in ROOT.iterdir():
        if top_level.name == ".git":
            continue
        paths.append(top_level)
        if top_level.is_dir() and not top_level.is_symlink():
            paths.extend(top_level.rglob("*"))
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if path.is_symlink():
            fail(f"unexpected workspace symlink: {relative}")
        allowed = relative in (
            ALLOWED_DIRECTORIES if path.is_dir() else ALLOWED_FILES
        )
        if not allowed:
            fail(f"unexpected workspace artifact: {relative}")


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


def require_int(event: dict[str, Any], key: str, label: str) -> int:
    value = event.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        fail(f"{label} has invalid {key}")
    return value


def verify_help_discovery() -> int:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value.get("event") != "appointment-help" or value.get("version") != 1:
        fail("appointmentctl top-level built-in help was not used")
    return require_int(value, "recorded_ns", "built-in help attestation")


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
        appointment["id"]: appointment for appointment in appointments
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


def appointment_digest(appointment: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(appointment)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = appointment_map(
        load_json(
            INITIAL_APPOINTMENTS_PATH,
            "protected initial appointment registry",
        ),
        "protected initial appointment registry",
    )
    current = appointment_map(
        load_json(CURRENT_APPOINTMENTS_PATH, "current appointment registry"),
        "current appointment registry",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial registry has an unexpected appointment set")
    target = initial[TARGET_ID]
    if (
        target.get("type") != APPOINTMENT_TYPE
        or target.get("status") != "scheduled"
        or target.get("cancellation_reason") is not None
    ):
        fail("hea-235 is not the requested scheduled Dental cleaning")
    if set(current) != set(initial):
        fail("an appointment was created or deleted")

    expected_target = dict(target)
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    revision = expected_target.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        fail("hea-235 has an invalid initial revision")
    expected_target["revision"] = revision + 1
    if current[TARGET_ID] != expected_target:
        fail("hea-235 was not cancelled exactly once for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another appointment was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
    help_recorded: int,
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
        or cancellation.get("before_status") != "scheduled"
        or cancellation.get("after_status") != "cancelled"
        or cancellation.get("updated") != 1
        or cancellation.get("outcome") != "committed"
    ):
        fail("the single cancellation request was incorrect or did not commit")
    cancel_started = require_int(
        cancellation,
        "started_ns",
        "cancellation event",
    )
    committed = require_int(
        cancellation,
        "committed_ns",
        "cancellation event",
    )
    if help_recorded >= cancel_started:
        fail("top-level built-in help was not used before appointment data")
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
        fail("the committed request was not ended by the required timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")
    elapsed = observed - cancel_started
    if elapsed < 750_000_000 or elapsed > 1_750_000_000:
        fail("state-changing request did not use the documented deadline")

    if (
        retrieval.get("appointment_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("appointment_type") != APPOINTMENT_TYPE
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("appointment_sha256")
        != appointment_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("hea-235 was not retrieved after the timeout completed")


def verify_result() -> None:
    if not RESULT_PATH.is_file() or RESULT_PATH.is_symlink():
        fail("cancellation_result.txt is missing")
    try:
        actual = RESULT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cancellation_result.txt is not readable UTF-8: {error}")
    if actual != EXPECTED_RESULT:
        fail("cancellation_result.txt is not the exact tool-grounded result")


def main() -> None:
    verify_workspace_shape()
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256)
    require_digest(
        INITIAL_APPOINTMENTS_PATH,
        EXPECTED_INITIAL_APPOINTMENTS_SHA256,
    )
    require_digest(
        SEED_APPOINTMENTS_PATH,
        EXPECTED_SEED_APPOINTMENTS_SHA256,
    )
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("appointmentctl is not executable")
    help_recorded = verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target, help_recorded)
    verify_result()
    print(
        "verification passed: one timed-out cancellation committed, "
        "hea-235 was retrieved as cancelled, and no retry or creation occurred"
    )


if __name__ == "__main__":
    main()
