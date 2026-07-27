#!/usr/bin/env python3
"""Protected verifier for the Pi facilities uncertain-commit workflow."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
SEED_SQL = PROTECTED / "facilities_seed.sql"
KEY_PATH = PROTECTED / "audit.key"
RUNTIME = ROOT / ".facilities"
DATABASE = RUNTIME / "facilities.sqlite3"
AUDIT_PATH = RUNTIME / "operations.jsonl"
REFERENCE_MARKER = ROOT / ".reference_solution"
REFERENCE_DRIVER = ROOT / "reference_driver.py"
REFERENCE_MARKER_DIGEST = (
    "35ab9cbc93affa2b6e16ec448ed111dbb6977ec0288fcc5d91536a29d6aca65a"
)
TARGET_ID = "fac-157"
TARGET_NAME = "Lighting repair"
REASON = "scheduled work is no longer needed"
EXPECTED_REPORT = (
    "Request fac-157 final status: cancelled.\n"
    "Conditional retry sent: no.\n"
)

PROTECTED_HASHES = {
    ".gitignore": "2759f9607fed04222aef8c469092da3cbe8d687f16662ab9846d7865c9ad2b27",
    ".protected/audit.key": "ea01cb5981706dd62f7d4f77fbd1632bc017fd7404816691d54e73e2bf3777db",
    ".protected/facilities_seed.sql": "a23225726fd367cae7f32d60b917de8b56d2a1150483ae7f7fc239f473d112f1",
    ".protected/setup.py": "11138da05755d3c02915bc88042c703763ff3ad46c49b881bc3eb4682c92b6c8",
    "FACILITIES.md": "6755b5ef86b59a1a8f0623790c3f2784da4ee28776b42e3b8c5643cdeb5bb337",
    "facilityctl": "aa377a77bd55452708c0554b1e277ab69fb2d69602f5ff32e15007b43026fa91",
}
BASE_ROOT_ENTRIES = {
    ".facilities",
    ".git",
    ".gitignore",
    ".protected",
    "FACILITIES.md",
    "cancellation_report.md",
    "facilityctl",
}
PROTECTED_ENTRIES = {
    "audit.key",
    "facilities_seed.sql",
    "setup.py",
    "verify.py",
}
RUNTIME_ENTRIES = {
    "facilities.sqlite3",
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
        require(sha256(path) == expected_hash, f"protected task input changed: {relative}")
    require(
        (ROOT / "facilityctl").stat().st_mode & 0o111 != 0,
        "facilityctl is not executable",
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
        "no valid facilities runtime was created by facilityctl",
    )
    observed_runtime = {path.name for path in RUNTIME.iterdir()}
    require(
        observed_runtime == RUNTIME_ENTRIES,
        "facilities runtime contains missing or unexpected artifacts",
    )
    for name in RUNTIME_ENTRIES:
        path = RUNTIME / name
        require(path.is_file() and not path.is_symlink(), f"invalid runtime file: {name}")


def snapshot(connection: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    connection.row_factory = sqlite3.Row
    return {
        "requests": [
            dict(row) for row in connection.execute("SELECT * FROM requests ORDER BY id")
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
                "SELECT * FROM service_control ORDER BY request_id, fault"
            )
        ],
    }


def baseline_snapshot() -> dict[str, list[dict[str, Any]]]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED_SQL.read_text(encoding="utf-8"))
        return snapshot(connection)
    except (OSError, sqlite3.Error) as error:
        raise VerificationError(f"protected facilities seed is invalid: {error}") from error
    finally:
        connection.close()


def runtime_snapshot() -> dict[str, list[dict[str, Any]]]:
    require(DATABASE.is_file() and not DATABASE.is_symlink(), "live database is missing")
    try:
        connection = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise VerificationError(f"live facilities database is unreadable: {error}") from error
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        require(integrity is not None and integrity[0] == "ok", "database integrity failed")
        return snapshot(connection)
    except sqlite3.Error as error:
        raise VerificationError(f"live facilities database is invalid: {error}") from error
    finally:
        connection.close()


def verify_state(
    baseline: dict[str, list[dict[str, Any]]],
    runtime: dict[str, list[dict[str, Any]]],
) -> None:
    initial = {row["id"]: row for row in baseline["requests"]}
    final = {row["id"]: row for row in runtime["requests"]}
    require(set(initial) == set(final), "the facilities request set changed")
    require(initial[TARGET_ID]["name"] == TARGET_NAME, "protected target identity is invalid")

    expected_target = dict(initial[TARGET_ID])
    expected_target.update(
        status="cancelled",
        cancellation_reason=REASON,
        version=expected_target["version"] + 1,
        cancellation_count=1,
    )
    require(
        final[TARGET_ID] == expected_target,
        "fac-157 was not cancelled exactly once with the exact requested reason",
    )
    for request_id, record in initial.items():
        if request_id != TARGET_ID:
            require(final[request_id] == record, f"out-of-scope request changed: {request_id}")

    require(
        runtime["notifications"] == baseline["notifications"],
        "a notification was created",
    )
    require(
        runtime["service_control"]
        == [
            {
                "request_id": TARGET_ID,
                "fault": "cancel_deadline_after_commit",
                "remaining": 0,
            }
        ],
        "the one-time lost response was not consumed exactly once",
    )


def load_events() -> list[dict[str, Any]]:
    require(
        AUDIT_PATH.is_file() and not AUDIT_PATH.is_symlink(),
        "authenticated facilities journal is missing",
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
        len(events) == 3,
        "expected only root help, one cancellation, and one settling retrieval",
    )
    require(
        [event.get("operation") for event in events] == ["help", "cancel", "get"],
        "the uncertain cancellation was not settled by an immediate direct retrieval",
    )

    help_event, cancellation, settling_get = events
    require(
        help_event.get("exit_code") == 0
        and help_event.get("response_delivered") is True
        and help_event.get("service_committed") is False
        and help_event.get("outcome") == "returned",
        "the executable's root help was not used successfully before data operations",
    )
    require(
        cancellation.get("record_id") == TARGET_ID
        and settling_get.get("record_id") == TARGET_ID,
        "a facilities operation accessed an ID outside the requested scope",
    )
    require(cancellation.get("reason") == REASON, "the cancellation reason changed")
    require(
        cancellation.get("status_before") == "active"
        and cancellation.get("status_after") == "cancelled"
        and cancellation.get("state_changed") is True
        and cancellation.get("service_committed") is True
        and cancellation.get("response_delivered") is False
        and cancellation.get("outcome") == "deadline_after_commit"
        and cancellation.get("exit_code") == 75,
        "the initial cancellation did not exercise the committed uncertain outcome",
    )
    require(
        settling_get.get("observed_status") == "cancelled"
        and settling_get.get("result_count") == 1
        and settling_get.get("service_committed") is False
        and settling_get.get("response_delivered") is True
        and settling_get.get("outcome") == "returned"
        and settling_get.get("exit_code") == 0,
        "the direct ID retrieval did not establish the committed cancellation",
    )

    prior_end: int | None = None
    for event in events:
        start = event.get("start_ns")
        end = event.get("end_ns")
        require(
            isinstance(start, int) and isinstance(end, int) and start <= end,
            "operation timing evidence is incomplete",
        )
        if prior_end is not None:
            require(prior_end < start, "facilities operations overlapped or were reordered")
        prior_end = end


def verify_report() -> None:
    path = ROOT / "cancellation_report.md"
    require(path.is_file() and not path.is_symlink(), "cancellation report is missing")
    require(
        path.read_text(encoding="utf-8") == EXPECTED_REPORT,
        "cancellation report is not grounded in the settling retrieval",
    )


def main() -> int:
    try:
        verify_protected_files()
        verify_workspace_scope()
        verify_state(baseline_snapshot(), runtime_snapshot())
        verify_execution(load_events())
        verify_report()
    except (VerificationError, KeyError, OSError, TypeError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: fac-157 was verified cancelled after its committed response was lost; no retry was sent"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
