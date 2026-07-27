#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0125."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_CASES_PATH = ROOT / ".cases" / "cases.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_CASES_PATH = RUNTIME_DIR / "cases.json"
INITIAL_CASES_PATH = ROOT / ".protected" / "initial_cases.json"
AUDIT_PATH = RUNTIME_DIR / "case-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "case-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "case-registry"
RECEIPT_PATH = ROOT / "cancellation_receipt.txt"

EXPECTED_EXECUTABLE_SHA256 = "c1aa2cfdee502b1b8d012f1333c500bd91ace6854d3711724b866823e4d3ca76"
EXPECTED_INITIAL_CASES_SHA256 = "761581e654a123871192def3914e063bd7fa25112e6381bd22f112980f403d27"
EXPECTED_SEED_CASES_SHA256 = "761581e654a123871192def3914e063bd7fa25112e6381bd22f112980f403d27"
EXPECTED_REFERENCE_SETUP_SHA256 = "c090e8f39eb8b5b77c804ba4ae6331a8b8f86cd4078bba8903814193dc7a6212"
EXPECTED_AUDIT_KEY_SHA256 = "8331643cd5e4cf6190462cd06d4fb973e2b7f70d5ec2382d25e9dacfa43a206f"
EXPECTED_GITIGNORE_SHA256 = "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
EXPECTED_CASE_DOC_SHA256 = "8f90798b822da44349aa30567e9dd22f153c8a745b12306cfe964f1a3465ca3a"
REFERENCE_MARKER_SHA256 = "ba15f410005497fdcaf97f5953a55496a2b94f9ea70bc2a1394342ebf1af68d8"

TARGET_ID = "sup-225"
OTHER_IDS = {"sup-125", "sup-625"}
NAME = "Damaged parcel 125"
REASON = "customer confirmed the parcel issue was filed twice"
EXPECTED_RECEIPT = (
    "Case: Damaged parcel 125 (sup-225)\n"
    "Verified status: cancelled\n"
    "Reason: customer confirmed the parcel issue was filed twice\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".cases/cases.json",
    ".gitignore",
    ".protected/audit.key",
    ".protected/initial_cases.json",
    ".protected/reference_setup.py",
    ".protected/runtime/case-audit.jsonl",
    ".protected/runtime/case-help.json",
    ".protected/runtime/case.lock",
    ".protected/runtime/cases.json",
    ".protected/verify.py",
    ".reference_solution",
    "cancellation_receipt.txt",
    "case-registry",
    "SUPPORT_CASES.md",
}
ALLOWED_DIRECTORIES = {".cases", ".protected", ".protected/runtime"}


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
        fail(f"{label} was not emitted intact by case-registry")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {"event": "case-registry-help", "version": 1}:
        fail("case-registry top-level built-in help was not used")


def case_map(
    document: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    cases = document.get("cases")
    if document.get("version") != 1 or not isinstance(cases, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(case, dict)
        and isinstance(case.get("id"), str)
        and case["id"]
        for case in cases
    ):
        fail(f"{label} contains an invalid support case")
    result = {case["id"]: case for case in cases}
    if len(result) != len(cases):
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


def case_digest(case: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(case)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = case_map(
        load_json(INITIAL_CASES_PATH, "protected initial registry"),
        "protected initial registry",
    )
    current = case_map(
        load_json(CURRENT_CASES_PATH, "current support registry"),
        "current support registry",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial registry has an unexpected case set")
    if (
        initial[TARGET_ID].get("name") != NAME
        or initial[TARGET_ID].get("status") != "active"
    ):
        fail("sup-225 is not the requested active support case")
    if set(current) != set(initial):
        fail("a support case was created or deleted")

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    revision = expected_target.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        fail("sup-225 has an invalid initial revision")
    expected_target["revision"] = revision + 1
    if current[TARGET_ID] != expected_target:
        fail("sup-225 was not cancelled exactly once for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another support case was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
) -> None:
    if len(events) != 3:
        fail(f"expected exactly three case events, found {len(events)}")
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
        cancellation.get("case_id") != TARGET_ID
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
        or timeout.get("case_id") != TARGET_ID
        or timeout.get("signal") != "SIGTERM"
        or timeout.get("outcome") != "timeout"
    ):
        fail("the committed request was not ended by the required transport timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")

    if (
        retrieval.get("case_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("name") != NAME
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("record_sha256") != case_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish the stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("sup-225 was not retrieved after the timeout completed")


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
    require_digest(INITIAL_CASES_PATH, EXPECTED_INITIAL_CASES_SHA256)
    require_digest(SEED_CASES_PATH, EXPECTED_SEED_CASES_SHA256)
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    require_digest(ROOT / "SUPPORT_CASES.md", EXPECTED_CASE_DOC_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("case-registry is not executable")
    verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target)
    verify_receipt()
    print(
        "verification passed: one timed-out cancellation committed, sup-225 "
        "was then retrieved as cancelled, and no retry or creation occurred"
    )


if __name__ == "__main__":
    main()
