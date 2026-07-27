#!/usr/bin/env python3
"""Protected verifier for the Pi appointment uncertain-commit workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
SEED_SQL = PROTECTED / "appointments_seed.sql"
KEY_PATH = PROTECTED / "audit.key"
RUNTIME = ROOT / ".appointments"
DATABASE = RUNTIME / "appointments.sqlite3"
AUDIT_PATH = RUNTIME / "operations.jsonl"
REFERENCE_MARKER = ROOT / ".reference_solution"
REFERENCE_DRIVER = ROOT / "reference_driver.py"
REFERENCE_COUNTER = RUNTIME / "reference-verify-count"
REFERENCE_MARKER_DIGEST = (
    "7cc9676576a382d49a7f43fdaea50fe9d293d63fab6db59a7d5be4ef30571400"
)
TARGET_ID = "hea-155"
TARGET_TITLE = "Dental cleaning"
REASON = "duplicate record entered during intake"

PROTECTED_HASHES = {
    ".gitignore": "c838b71b22afb6d665e359a02eb84f7e181ada21b0c09b6b288f16979f8e0f76",
    ".protected/audit.key": "c6a88a520c10b290873a82e7e2c715d8c3b99130ead419bfedd82bed4ccf7b2f",
    ".protected/appointments_seed.sql": "ee0f01db99ea35ca95c328e61055db731aca1c27c6abc9658757187d503993f9",
    ".protected/setup.py": "38064ea97f522780039d75c278de237a419c62109df67b395a5cb10cc00a32ed",
    "APPOINTMENTS.md": "a9ec890b214e1174b7053905c3918ce1541859205f354273250a5053276ea386",
    "appointmentctl": "fd784a6fe6e2aac69c6fb6b4b70d835eb1b42b1ea791219e0ab957dad2e30e6b",
}
BASE_ROOT_ENTRIES = {
    ".appointments",
    ".git",
    ".gitignore",
    ".protected",
    "APPOINTMENTS.md",
    "appointmentctl",
}
PROTECTED_ENTRIES = {
    "appointments_seed.sql",
    "audit.key",
    "setup.py",
    "verify.py",
}
BASE_RUNTIME_ENTRIES = {
    "appointments.sqlite3",
    "operations.jsonl",
    "service.lock",
}


class VerificationError(AssertionError):
    """A deterministic verification failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_key() -> bytes:
    try:
        return bytes.fromhex(KEY_PATH.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        raise VerificationError(f"protected signing key is invalid: {error}") from error


def reference_mode() -> bool:
    return (
        REFERENCE_MARKER.is_file()
        and not REFERENCE_MARKER.is_symlink()
        and sha256(REFERENCE_MARKER) == REFERENCE_MARKER_DIGEST
        and REFERENCE_DRIVER.is_file()
        and not REFERENCE_DRIVER.is_symlink()
    )


def verify_protected_files() -> None:
    for relative, expected_hash in PROTECTED_HASHES.items():
        path = ROOT / relative
        require(
            path.is_file() and not path.is_symlink(),
            f"protected task input is missing or linked: {relative}",
        )
        require(
            sha256(path) == expected_hash,
            f"protected task input changed: {relative}",
        )


def verify_workspace_scope() -> None:
    require(
        PROTECTED.is_dir() and not PROTECTED.is_symlink(),
        "protected task directory is missing or linked",
    )
    protected_entries = {path.name for path in PROTECTED.iterdir()}
    require(
        protected_entries == PROTECTED_ENTRIES,
        "protected task directory contains missing or unexpected artifacts",
    )
    for name in PROTECTED_ENTRIES:
        path = PROTECTED / name
        require(path.is_file() and not path.is_symlink(), f"invalid protected file: {name}")

    allowed = set(BASE_ROOT_ENTRIES)
    if reference_mode():
        allowed.update({REFERENCE_MARKER.name, REFERENCE_DRIVER.name})
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    require(
        not unexpected,
        "unexpected scratch artifact at sandbox root: " + ", ".join(unexpected),
    )

    require(
        RUNTIME.is_dir() and not RUNTIME.is_symlink(),
        "no valid appointment runtime was created by appointmentctl",
    )
    expected_runtime = set(BASE_RUNTIME_ENTRIES)
    if reference_mode() and REFERENCE_COUNTER.is_file():
        expected_runtime.add(REFERENCE_COUNTER.name)
    observed_runtime = {path.name for path in RUNTIME.iterdir()}
    require(
        observed_runtime == expected_runtime,
        "appointment runtime contains missing or unexpected artifacts",
    )
    for name in BASE_RUNTIME_ENTRIES:
        path = RUNTIME / name
        require(path.is_file() and not path.is_symlink(), f"invalid runtime file: {name}")


def snapshot(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    connection.row_factory = sqlite3.Row
    return {
        "appointments": [
            dict(row)
            for row in connection.execute("SELECT * FROM appointments ORDER BY id")
        ],
        "notifications": [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM notifications ORDER BY notification_id"
            )
        ],
        "service_control": [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM service_control ORDER BY appointment_id, fault"
            )
        ],
    }


def baseline_snapshot() -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_SQL.read_text(encoding="utf-8"))
        return snapshot(connection)
    except (OSError, sqlite3.Error) as error:
        raise VerificationError(f"protected appointment seed is invalid: {error}") from error
    finally:
        connection.close()


def runtime_snapshot() -> dict[str, list[dict[str, Any]]]:
    require(DATABASE.is_file() and not DATABASE.is_symlink(), "live database is missing")
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise VerificationError(f"live appointment database is unreadable: {error}") from error
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        require(integrity is not None and integrity[0] == "ok", "database integrity failed")
        return snapshot(connection)
    except sqlite3.Error as error:
        raise VerificationError(f"live appointment database is invalid: {error}") from error
    finally:
        connection.close()


def verify_state(
    baseline: dict[str, list[dict[str, Any]]],
    runtime: dict[str, list[dict[str, Any]]],
) -> None:
    initial = {row["id"]: row for row in baseline["appointments"]}
    final = {row["id"]: row for row in runtime["appointments"]}
    require(set(initial) == set(final), "the appointment record set changed")
    require(
        initial[TARGET_ID]["title"] == TARGET_TITLE,
        "protected target identity is invalid",
    )

    expected_target = dict(initial[TARGET_ID])
    expected_target.update(
        status="cancelled",
        cancellation_reason=REASON,
        version=expected_target["version"] + 1,
        cancellation_count=1,
    )
    require(
        final[TARGET_ID] == expected_target,
        "hea-155 was not cancelled exactly once with the exact requested reason",
    )
    for appointment_id, record in initial.items():
        if appointment_id != TARGET_ID:
            require(
                final[appointment_id] == record,
                f"out-of-scope appointment changed: {appointment_id}",
            )

    require(
        runtime["notifications"] == baseline["notifications"],
        "a notification was created",
    )
    expected_control = [
        {
            "appointment_id": TARGET_ID,
            "fault": "cancel_timeout_without_commit",
            "remaining": 0,
        }
    ]
    require(
        runtime["service_control"] == expected_control,
        "the one-time uncertain timeout was not consumed exactly once",
    )


def load_events() -> list[dict[str, Any]]:
    require(
        AUDIT_PATH.is_file() and not AUDIT_PATH.is_symlink(),
        "authenticated appointment journal is missing",
    )
    events: list[dict[str, Any]] = []
    previous = "GENESIS"
    for sequence, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"operation journal entry {sequence} is invalid JSON"
            ) from error
        require(isinstance(event, dict), f"operation journal entry {sequence} is invalid")
        signature = event.get("signature")
        unsigned = {key: value for key, value in event.items() if key != "signature"}
        expected = hmac.new(audit_key(), canonical(unsigned), hashlib.sha256).hexdigest()
        require(
            event.get("sequence") == sequence
            and event.get("previous") == previous
            and isinstance(signature, str)
            and hmac.compare_digest(signature, expected),
            f"operation journal entry {sequence} was not authentically emitted intact",
        )
        previous = signature
        events.append(event)
    return events


def verify_execution(events: list[dict[str, Any]]) -> None:
    require(
        len(events) == 4,
        "expected root help, one cancellation, one direct retrieval, and one retry",
    )
    require(
        [event.get("operation") for event in events]
        == ["help", "cancel", "get", "cancel"],
        "the uncertain cancellation was not retrieved by ID before the single retry",
    )

    help_event, first, direct_get, retry = events
    require(
        help_event.get("exit_code") == 0
        and help_event.get("response_delivered") is True
        and help_event.get("service_committed") is False
        and help_event.get("outcome") == "returned",
        "the executable's root help was not used successfully before data operations",
    )
    require(
        all(event.get("record_id") == TARGET_ID for event in events[1:]),
        "an appointment operation accessed an ID outside the requested scope",
    )

    require(first.get("reason") == REASON, "the initial cancellation reason changed")
    require(
        first.get("status_before") == "active"
        and first.get("status_after") == "active"
        and first.get("state_changed") is False
        and first.get("service_committed") is False
        and first.get("response_delivered") is False
        and first.get("outcome") == "timeout_unknown"
        and first.get("exit_code") == 75,
        "the initial cancellation did not exercise the uncertain timeout",
    )

    require(
        direct_get.get("observed_status") == "active"
        and direct_get.get("result_count") == 1
        and direct_get.get("service_committed") is False
        and direct_get.get("response_delivered") is True
        and direct_get.get("outcome") == "returned"
        and direct_get.get("exit_code") == 0,
        "the direct ID retrieval did not authoritatively observe an active appointment",
    )

    require(retry.get("reason") == REASON, "the conditional retry was not identical")
    require(
        retry.get("status_before") == "active"
        and retry.get("status_after") == "cancelled"
        and retry.get("state_changed") is True
        and retry.get("service_committed") is True
        and retry.get("response_delivered") is True
        and retry.get("outcome") == "committed"
        and retry.get("exit_code") == 0,
        "the single conditional retry did not commit the requested cancellation",
    )

    timestamps: list[int] = []
    for event in events:
        start = event.get("start_ns")
        end = event.get("end_ns")
        require(
            isinstance(start, int) and isinstance(end, int) and start <= end,
            "operation timing evidence is incomplete",
        )
        timestamps.extend([start, end])
    require(
        all(timestamps[index] < timestamps[index + 1] for index in (1, 3, 5)),
        "the executable operations were not completed in the required sequence",
    )


def finish_reference_validation() -> None:
    if not reference_mode():
        return
    if REFERENCE_COUNTER.is_file() and not REFERENCE_COUNTER.is_symlink():
        require(
            REFERENCE_COUNTER.read_text(encoding="utf-8") == "1\n",
            "reference verification counter is invalid",
        )
        shutil.rmtree(RUNTIME)
    else:
        REFERENCE_COUNTER.write_text("1\n", encoding="utf-8")


def main() -> int:
    try:
        verify_protected_files()
        verify_workspace_scope()
        verify_state(baseline_snapshot(), runtime_snapshot())
        verify_execution(load_events())
        finish_reference_validation()
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: hea-155 was retrieved after an uncertain timeout and cancelled by one retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
