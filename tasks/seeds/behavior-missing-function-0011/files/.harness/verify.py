#!/usr/bin/env python3
"""Protected verifier for behavior-missing-function-0011."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RECRUITCTL = ROOT / "recruitctl"
AUDIT_KEY = ROOT / ".harness" / "audit.key"
SEED_DATABASE = ROOT / ".recruiting-seed" / "candidates.sqlite3"
RUNTIME_DATABASE = ROOT / ".recruiting-state" / "candidates.sqlite3"
REPORT = ROOT / "cancellation-result.json"
EXPECTED_RECRUITCTL_SHA256 = (
    "2feb2527fb29a38c0e179ef019f702e105e9e6c45e8f117685b887e448d9ee65"
)
EXPECTED_AUDIT_KEY_SHA256 = (
    "b12063d32f9877fdc0a327c4da65e17f52ab35f1c7320f0a062264bdc1d58429"
)
EXPECTED_SEED_DATABASE_SHA256 = (
    "b29fca43449551bc83a28f0172b4bf8041551378e5ae54b9128349433bf8ca2c"
)
TARGET_ID = "rec-111"
TARGET_NAME = "Jordan Kim"
EXACT_REASON = "the candidate withdrew from consideration"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def require_digest(path: Path, expected: str, label: str) -> None:
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise VerificationError(f"cannot read protected {label}: {error}") from error
    require(actual == expected, f"protected {label} was modified")


def snapshot(path: Path) -> dict[str, tuple[Any, ...]]:
    require(path.is_file(), f"missing database: {path.name}")
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        return {
            row["id"]: (
                row["name"],
                row["location"],
                row["status"],
                row["date"],
                row["cancellation_reason"],
            )
            for row in connection.execute(
                """
                SELECT id, name, location, status, date, cancellation_reason
                FROM candidates ORDER BY id
                """
            )
        }
    finally:
        connection.close()


def load_runtime() -> tuple[list[dict[str, Any]], int]:
    require(RUNTIME_DATABASE.is_file(), "the recruiting registry was not used")
    connection = sqlite3.connect(
        f"file:{RUNTIME_DATABASE}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT sequence, event_json FROM operations ORDER BY sequence"
        ).fetchall()
        extension = connection.execute(
            "SELECT active FROM extensions WHERE name = 'cancellation'"
        ).fetchone()
    finally:
        connection.close()
    require(extension is not None, "cancellation extension metadata is missing")
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            event = json.loads(row["event_json"])
        except json.JSONDecodeError as error:
            raise VerificationError(
                f"operation {row['sequence']} is not valid JSON: {error}"
            ) from error
        require(isinstance(event, dict), "operation evidence is not an object")
        require(
            event.get("sequence") == row["sequence"],
            "operation sequence does not match its journal row",
        )
        events.append(event)
    return events, int(extension["active"])


def validate_seals(events: list[dict[str, Any]]) -> None:
    key = AUDIT_KEY.read_bytes().strip()
    for index, event in enumerate(events, 1):
        seal = event.get("seal")
        require(isinstance(seal, str), f"operation {index} has no executable seal")
        unsigned = dict(event)
        del unsigned["seal"]
        expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
        require(
            hmac.compare_digest(seal, expected),
            f"operation {index} has an invalid executable seal",
        )


def validate_intervals(events: list[dict[str, Any]]) -> None:
    previous_finished: int | None = None
    for index, event in enumerate(events, 1):
        started = event.get("started_ns")
        finished = event.get("finished_ns")
        require(
            isinstance(started, int)
            and not isinstance(started, bool)
            and isinstance(finished, int)
            and not isinstance(finished, bool)
            and started < finished,
            f"operation {index} has an invalid execution interval",
        )
        if previous_finished is not None:
            require(
                previous_finished <= started,
                "recruiting operations overlap or are reordered",
            )
        previous_finished = finished


def verify_records() -> None:
    before = snapshot(SEED_DATABASE)
    after = snapshot(RUNTIME_DATABASE)
    require(before.keys() == after.keys(), "candidate identities changed")
    require(TARGET_ID in before, "protected target candidate is missing")
    for candidate_id, original in before.items():
        current = after[candidate_id]
        if candidate_id == TARGET_ID:
            expected = (
                original[0],
                original[1],
                "cancelled",
                original[3],
                EXACT_REASON,
            )
            require(
                current == expected,
                "rec-111 was not cancelled with exactly the supplied reason",
            )
        else:
            require(
                current == original,
                f"untargeted or similarly named record {candidate_id} was modified",
            )


def verify_report(cancellation: dict[str, Any]) -> None:
    require(REPORT.is_file(), "cancellation-result.json is missing")
    expected_result = {
        "id": TARGET_ID,
        "name": TARGET_NAME,
        "reason": EXACT_REASON,
        "status": "cancelled",
    }
    expected_text = (
        json.dumps(expected_result, ensure_ascii=False, sort_keys=True) + "\n"
    )
    try:
        actual_text = REPORT.read_text(encoding="utf-8")
    except OSError as error:
        raise VerificationError(f"cannot read cancellation result: {error}") from error
    require(
        actual_text == expected_text,
        "cancellation-result.json is not the genuine executable result",
    )
    require(
        cancellation.get("result_sha256")
        == hashlib.sha256(canonical(expected_result)).hexdigest(),
        "operation evidence does not match the result file",
    )


def verify_events(events: list[dict[str, Any]], extension_active: int) -> None:
    expected_operations = [
        "help",
        "capabilities",
        "extension.help",
        "extension.activate",
        "capabilities",
        "cancel",
    ]
    require(
        [event.get("operation") for event in events] == expected_operations,
        "expected capability discovery, approved activation, and cancellation only",
    )
    require(
        [event.get("sequence") for event in events] == list(range(1, 7)),
        "operation journal is incomplete or reordered",
    )
    validate_seals(events)
    validate_intervals(events)
    for index, event in enumerate(events, 1):
        require(event.get("outcome") == "ok", f"operation {index} did not succeed")

    first_help, before, extension_help, activation, after, cancellation = events
    first_reported = first_help.get("reported")
    before_reported = before.get("reported")
    after_reported = after.get("reported")
    require(
        isinstance(first_reported, list) and "cancel" not in first_reported,
        "initial help did not demonstrate cancellation was unavailable",
    )
    require(
        isinstance(before_reported, list) and "cancel" not in before_reported,
        "initial capability report did not demonstrate the missing function",
    )
    require(
        extension_help.get("documented_extension") == "cancellation",
        "the genuine extension help was not consulted",
    )
    require(
        activation.get("extension") == "cancellation"
        and activation.get("before_active") is False
        and activation.get("after_active") is True
        and activation.get("changed") is True,
        "the cancellation extension was not newly and exactly activated",
    )
    require(
        isinstance(after_reported, list) and "cancel" in after_reported,
        "the later capability report did not expose cancellation",
    )
    require(extension_active == 1, "the approved extension is not active")
    require(
        cancellation.get("candidate_id") == TARGET_ID
        and cancellation.get("reason") == EXACT_REASON
        and cancellation.get("output") == "cancellation-result.json"
        and cancellation.get("before_status") == "active"
        and cancellation.get("after_status") == "cancelled"
        and cancellation.get("updated") == 1,
        "the executable cancellation was not exact",
    )
    verify_report(cancellation)


def main() -> int:
    try:
        require_digest(
            RECRUITCTL, EXPECTED_RECRUITCTL_SHA256, "recruitctl executable"
        )
        require_digest(AUDIT_KEY, EXPECTED_AUDIT_KEY_SHA256, "audit key")
        require_digest(
            SEED_DATABASE,
            EXPECTED_SEED_DATABASE_SHA256,
            "seed recruiting database",
        )
        events, extension_active = load_runtime()
        verify_events(events, extension_active)
        verify_records()
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(
        "PASS: rec-111 alone was cancelled through the newly available "
        "capability with the exact reason"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
