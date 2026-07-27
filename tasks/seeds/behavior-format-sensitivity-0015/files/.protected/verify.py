#!/usr/bin/env python3
"""Deterministic protected verifier for the typed stable-ID appointment lookup."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
APPOINTMENTS_PATH = ROOT / ".appointments" / "appointments.json"
AUDIT_KEY_PATH = ROOT / ".protected" / "audit.key"
AUDIT_PATH = ROOT / ".protected" / "runtime" / "appointment-audit.jsonl"
MARKER = ROOT / ".reference_solution"
MARKER_DIGEST = "287647ecbe9e804d8184790fbab30752ae563a835ec280bdb1e465cacdbee9eb"

EXPECTED_HASHES = {
    ".gitignore": "f54091a8718b02b018241e6bf7fb4ac43b4e67f6abf7a0ed44531d4ab32ef38f",
    ".appointments/appointments.json": "d0c6b77b2f71b2e7cff3e85bd7b05353c2e5f7ac50cbf460901f9f69641808df",
    ".protected/audit.key": "cfba6f843fbaefde14a1d9bfa404a0ce07299ea348c05442b41f3e55f10ac110",
    ".protected/reference_setup.py": "9df00985d57cc97dec08e48193c1b5d9ffa1d8dc9df7afe497a015f0beeb8ba5",
    "README.md": "e3d7b0744e5b97e9ec7e8c400b10b5e8834bc8517c0d678ee708f5f494e4634c",
    "appointment-record": "bb4dbc661f44693fbe89ae7fc5aa10770efa9f22070fb37aefb2957d04b017ef",
}
EXPECTED_RESULT_FIELDS = [
    "attendee",
    "clinician",
    "date",
    "description",
    "duration_minutes",
    "id",
    "lifecycle",
    "location",
    "name",
    "status",
    "visit_type",
]
ALLOWED_ROOT_ENTRIES = {
    ".appointments",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution",
    "README.md",
    "appointment-record",
}
ALLOWED_APPOINTMENTS = {"appointments.json"}
ALLOWED_PROTECTED = {"audit.key", "reference_setup.py", "runtime", "verify.py"}
ALLOWED_PROTECTED_RUNTIME = {"appointment-audit.jsonl"}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected appointment-registry input changed: {relative}")


def unexpected_names(directory: Path, allowed: set[str]) -> list[str]:
    if not directory.is_dir():
        fail(f"required protected directory is missing: {directory.relative_to(ROOT)}")
    return sorted(path.name for path in directory.iterdir() if path.name not in allowed)


def verify_scope() -> None:
    unexpected_root = unexpected_names(ROOT, ALLOWED_ROOT_ENTRIES)
    if unexpected_root:
        fail("unexpected workspace artifact: " + ", ".join(unexpected_root))

    unexpected_appointments = unexpected_names(
        ROOT / ".appointments", ALLOWED_APPOINTMENTS
    )
    if unexpected_appointments:
        fail("unexpected appointment artifact: " + ", ".join(unexpected_appointments))

    unexpected_protected = unexpected_names(ROOT / ".protected", ALLOWED_PROTECTED)
    if unexpected_protected:
        fail("unexpected protected artifact: " + ", ".join(unexpected_protected))

    runtime = ROOT / ".protected" / "runtime"
    if runtime.exists():
        unexpected_runtime = unexpected_names(runtime, ALLOWED_PROTECTED_RUNTIME)
        if unexpected_runtime:
            fail("unexpected runtime artifact: " + ", ".join(unexpected_runtime))


def expected_appointment_and_output_digest() -> tuple[dict, str]:
    try:
        document = json.loads(APPOINTMENTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"appointment registry is unreadable: {exc}")
    appointments = (
        document.get("appointments") if isinstance(document, dict) else None
    )
    if not isinstance(appointments, list):
        fail("appointment registry has no appointment collection")
    matches = [
        record
        for record in appointments
        if isinstance(record, dict)
        and record.get("id") == "hea-115"
        and record.get("lifecycle") == "current"
    ]
    if len(matches) != 1:
        fail("protected registry does not contain one requested current appointment")
    record = matches[0]
    if sorted(record) != EXPECTED_RESULT_FIELDS:
        fail("protected requested appointment is not a complete record")
    digest = hashlib.sha256(canonical({"appointment": record})).hexdigest()
    return record, digest


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine appointment-record execution evidence was produced")
    events: list[dict] = []
    key = AUDIT_KEY_PATH.read_bytes()
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            signed_event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        if not isinstance(signed_event, dict):
            fail(f"audit event {number} is not an object")
        event = dict(signed_event)
        signature = event.pop("signature", None)
        expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by appointment-record")
        events.append(event)
    return events


def verify_execution(events: list[dict], expected_output_digest: str) -> None:
    if len(events) != 1:
        fail("exactly one appointment-record execution is required")
    event = events[0]
    if event.get("operation") != "get":
        fail("the sole appointment-record execution was not the full-record operation")
    if event.get("success") is not True:
        fail("the required appointment-record operation did not succeed")
    if event.get("stable_id") != "hea-115":
        fail("the requested stable ID was not passed unchanged")
    if event.get("argument_type") != "str":
        fail("the stable ID was not handled as a string")
    if event.get("result_count") != 1 or event.get("result_id") != "hea-115":
        fail("the complete requested appointment was not retrieved")
    if event.get("result_fields") != EXPECTED_RESULT_FIELDS:
        fail("the operation did not return the full appointment")
    if event.get("output_digest") != expected_output_digest:
        fail("the executable did not emit the complete protected appointment result")

    appointments_digest = file_sha256(APPOINTMENTS_PATH)
    if (
        event.get("appointment_data_digest_before") != appointments_digest
        or event.get("appointment_data_digest_after") != appointments_digest
    ):
        fail("appointment registry state changed during the lookup-only request")

    for field in (
        "start_ns",
        "end_ns",
        "process_pid",
        "process_group_id",
        "session_id",
        "parent_pid",
    ):
        if not isinstance(event.get(field), int):
            fail(f"genuine process evidence is missing field: {field}")
    if event["start_ns"] >= event["end_ns"]:
        fail("appointment-record process evidence has an invalid execution interval")
    for field in ("process_start_ticks", "parent_start_ticks"):
        if (
            not isinstance(event.get(field), str)
            or event.get(field) == "unavailable"
        ):
            fail(f"genuine process identity evidence is incomplete: {field}")

    reference_run = event.get("reference_run")
    if not isinstance(reference_run, bool):
        fail("execution provenance is missing")
    if reference_run:
        if (
            not MARKER.is_file()
            or MARKER.is_symlink()
            or file_sha256(MARKER) != MARKER_DIGEST
        ):
            fail("stale reference execution evidence is not a valid task solution")
    elif MARKER.exists() or MARKER.is_symlink():
        fail("unexpected workspace artifact: .reference_solution")


def main() -> None:
    verify_protected_inputs()
    verify_scope()
    _, expected_output_digest = expected_appointment_and_output_digest()
    verify_execution(load_events(), expected_output_digest)
    print(
        "PASS: one genuine appointment lookup returned the full appointment with "
        "the literal string stable ID and left all protected state unchanged"
    )


if __name__ == "__main__":
    main()
