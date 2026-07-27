#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0132."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_COURSES_PATH = ROOT / ".registrar" / "courses.json"
SEED_POLICY_PATH = ROOT / ".registrar" / "transient_policy.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_COURSES_PATH = RUNTIME_DIR / "courses.json"
ATTEMPTS_PATH = RUNTIME_DIR / "availability-attempts.json"
AUDIT_PATH = RUNTIME_DIR / "registrar-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "registrar-help.json"
INITIAL_COURSES_PATH = ROOT / ".protected" / "initial_courses.json"
INITIAL_POLICY_PATH = ROOT / ".protected" / "initial_transient_policy.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "registrarctl"
REPORT_PATH = ROOT / "availability_report.md"

EXPECTED_EXECUTABLE_SHA256 = "bfc502118df9a05a7390a6ee8bf285ec068d6453e22c612bb25ebf56bfff1de4"
EXPECTED_INITIAL_COURSES_SHA256 = "a74906f0a64387160b8347808403db8a7aedb7dbba372b051f1395b7be56c688"
EXPECTED_SEED_COURSES_SHA256 = "a74906f0a64387160b8347808403db8a7aedb7dbba372b051f1395b7be56c688"
EXPECTED_INITIAL_POLICY_SHA256 = "91cc848205c421042fc08962d559e07e553e28d2dc1fce095294b5d3ea79bc40"
EXPECTED_SEED_POLICY_SHA256 = "91cc848205c421042fc08962d559e07e553e28d2dc1fce095294b5d3ea79bc40"
EXPECTED_AUDIT_KEY_SHA256 = "de46a1de181e26f27ffe03ba125beadc7bcf5573b8b970b72af5f5eb2bb72791"
EXPECTED_GITIGNORE_SHA256 = "cc519c2a4b79d325c678ea6e1a158b1409876ceb623af9e3b20c7add5b67dd7a"

DATE = "2026-11-15"
MICRO_QUERY = {
    "course": "Microeconomics 132",
    "location": "Downtown",
    "date": DATE,
}
HISTORY_QUERY = {
    "course": "Modern History 132",
    "location": "North Campus",
    "date": DATE,
}
EXPECTED_REPORT = (
    "- Microeconomics 132 at Downtown on 2026-11-15: available.\n"
    "- Modern History 132 at North Campus on 2026-11-15: unavailable.\n"
    "- Retry: Modern History 132 at North Campus succeeded on the one permitted "
    "retry after a retryable transient error.\n"
)
ALLOWED_FILES = {
    ".gitignore",
    ".registrar/courses.json",
    ".registrar/transient_policy.json",
    ".protected/audit.key",
    ".protected/initial_courses.json",
    ".protected/initial_transient_policy.json",
    ".protected/runtime/availability-attempts.json",
    ".protected/runtime/courses.json",
    ".protected/runtime/registrar-audit.jsonl",
    ".protected/runtime/registrar-help.json",
    ".protected/runtime/registrar.lock",
    ".protected/verify.py",
    ".reference_solution.py",
    "availability_report.md",
    "registrarctl",
}
ALLOWED_DIRECTORIES = {".registrar", ".protected", ".protected/runtime"}


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
        fail(f"{label} was not emitted intact by registrarctl")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {
        "event": "registrarctl-help",
        "arguments": ["--help"],
        "version": 1,
    }:
        fail("registrarctl top-level built-in help was not used")


def verify_state() -> None:
    initial_courses = load_json(
        INITIAL_COURSES_PATH,
        "protected initial course registry",
    )
    current_courses = load_json(
        CURRENT_COURSES_PATH,
        "current course registry",
    )
    seed_courses = load_json(SEED_COURSES_PATH, "seed course registry")
    if current_courses != initial_courses or seed_courses != initial_courses:
        fail("a course record was created, changed, deleted, or replaced")

    initial_policy = load_json(
        INITIAL_POLICY_PATH,
        "protected initial transient policy",
    )
    seed_policy = load_json(SEED_POLICY_PATH, "seed transient policy")
    if seed_policy != initial_policy:
        fail("the transient policy was changed or replaced")


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


def query_from_event(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "course": event.get("course"),
        "location": event.get("location"),
        "date": event.get("date"),
    }


def query_key(query: dict[str, str]) -> str:
    return json.dumps(
        query,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def verify_attempt_counts() -> None:
    attempts = validate_seal(
        load_json(ATTEMPTS_PATH, "availability attempt counters"),
        "availability attempt counters",
    )
    expected = {
        "version": 1,
        "counts": {
            query_key(MICRO_QUERY): 1,
            query_key(HISTORY_QUERY): 2,
        },
    }
    if attempts != expected:
        fail("availability attempts were not exactly one microeconomics and two history")


def verify_success(
    event: dict[str, Any],
    query: dict[str, str],
    availability: str,
    attempt: int,
    record_id: str,
    label: str,
) -> tuple[int, int]:
    if (
        event.get("operation") != "availability"
        or query_from_event(event) != query
        or event.get("attempt") != attempt
        or event.get("outcome") != "ok"
        or event.get("availability") != availability
        or event.get("record_id") != record_id
    ):
        fail(f"{label} did not record the required successful availability")
    started = require_int(event, "started_ns", label)
    finished = require_int(event, "finished_ns", label)
    if started >= finished:
        fail(f"{label} has an invalid execution interval")
    return started, finished


def verify_execution(events: list[dict[str, Any]]) -> None:
    if len(events) != 3:
        fail(f"expected exactly three course-data operations, found {len(events)}")
    if [event.get("sequence") for event in events] != [1, 2, 3]:
        fail("execution journal sequence is incomplete or reordered")
    if any(event.get("operation") != "availability" for event in events):
        fail("a course was retrieved, listed, created, changed, or cancelled")

    micro_events = [
        event for event in events if query_from_event(event) == MICRO_QUERY
    ]
    history_events = [
        event for event in events if query_from_event(event) == HISTORY_QUERY
    ]
    if len(micro_events) != 1 or len(history_events) != 2:
        fail("the exact requested availability checks were not used")

    micro = micro_events[0]
    history_initial = next(
        (event for event in history_events if event.get("attempt") == 1),
        None,
    )
    history_retry = next(
        (event for event in history_events if event.get("attempt") == 2),
        None,
    )
    if history_initial is None or history_retry is None:
        fail("history attempts are missing or incorrectly numbered")

    micro_started, micro_finished = verify_success(
        micro,
        MICRO_QUERY,
        "available",
        1,
        "edu-232",
        "microeconomics check",
    )
    if (
        history_initial.get("operation") != "availability"
        or history_initial.get("outcome") != "transient-error"
        or history_initial.get("code") != "temporary_unavailable"
        or history_initial.get("transient") is not True
        or history_initial.get("retryable") is not True
        or history_initial.get("committed") is not False
    ):
        fail("history first attempt was not the expected retryable transient error")
    history_started = require_int(
        history_initial,
        "started_ns",
        "history initial check",
    )
    history_finished = require_int(
        history_initial,
        "finished_ns",
        "history initial check",
    )
    if history_started >= history_finished:
        fail("history initial check has an invalid execution interval")

    if max(micro_started, history_started) >= min(
        micro_finished,
        history_finished,
    ):
        fail("the two independent initial checks did not execute concurrently")

    micro_parent = require_int(micro, "parent_pid", "microeconomics check")
    history_parent = require_int(
        history_initial,
        "parent_pid",
        "history initial check",
    )
    if micro_parent == history_parent:
        fail("the initial checks were not issued through sibling Bash executions")
    if require_int(micro, "pid", "microeconomics check") == require_int(
        history_initial,
        "pid",
        "history initial check",
    ):
        fail("the initial checks did not use independent processes")

    retry_started, _retry_finished = verify_success(
        history_retry,
        HISTORY_QUERY,
        "unavailable",
        2,
        "edu-632",
        "history retry",
    )
    if retry_started <= max(micro_finished, history_finished):
        fail("history was retried before both initial checks completed")
    retry_parent = require_int(history_retry, "parent_pid", "history retry")
    if retry_parent in {micro_parent, history_parent}:
        fail("the retry was not issued in a later Bash execution")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("availability_report.md is missing")
    try:
        actual = REPORT_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"availability_report.md is not readable UTF-8: {error}")
    if actual != EXPECTED_REPORT:
        fail("availability_report.md is not the exact tool-grounded three-bullet report")


def main() -> None:
    verify_workspace_shape()
    require_digest(EXECUTABLE_PATH, EXPECTED_EXECUTABLE_SHA256)
    require_digest(INITIAL_COURSES_PATH, EXPECTED_INITIAL_COURSES_SHA256)
    require_digest(SEED_COURSES_PATH, EXPECTED_SEED_COURSES_SHA256)
    require_digest(INITIAL_POLICY_PATH, EXPECTED_INITIAL_POLICY_SHA256)
    require_digest(SEED_POLICY_PATH, EXPECTED_SEED_POLICY_SHA256)
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("registrarctl is not executable")
    verify_help_discovery()
    verify_state()
    verify_attempt_counts()
    verify_execution(load_audit())
    verify_report()
    print(
        "verification passed: the initial checks overlapped, the successful "
        "microeconomics result was retained, only the transient history check "
        "was retried, records were unchanged, and the report has exactly three bullets"
    )


if __name__ == "__main__":
    main()
