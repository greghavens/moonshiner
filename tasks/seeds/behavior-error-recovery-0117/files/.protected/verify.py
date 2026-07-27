#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0117."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_REQUESTS_PATH = ROOT / ".facilities" / "requests.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_REQUESTS_PATH = RUNTIME_DIR / "requests.json"
INITIAL_REQUESTS_PATH = ROOT / ".protected" / "initial_requests.json"
AUDIT_PATH = RUNTIME_DIR / "facilities-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "facilities-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "facilitiesctl"
RECEIPT_PATH = ROOT / "cancellation_receipt.txt"

EXPECTED_EXECUTABLE_SHA256 = "526bb6602602468ec1ad22570dd3d918c6ca5544734c8fa63c89af26fbb6bad2"
EXPECTED_INITIAL_REQUESTS_SHA256 = "b863c425c16e5ed7d3e466e5af5260e6c956d5a70dc4598f405506f15ed0ed5a"
EXPECTED_SEED_REQUESTS_SHA256 = "b863c425c16e5ed7d3e466e5af5260e6c956d5a70dc4598f405506f15ed0ed5a"
EXPECTED_REFERENCE_SETUP_SHA256 = "d9d696e4c6cc322dfc097b09468779a93314b33229f8a2a5c00dc154628da6d6"
EXPECTED_AUDIT_KEY_SHA256 = "3b95d73e239c2ada703febdd43991a8f9a375ae06d546877d8d942f1f1b8b739"
EXPECTED_GITIGNORE_SHA256 = "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
EXPECTED_FACILITIES_DOC_SHA256 = "c877f51006a35570f1bbb26d1779991046c7342f3554dc5c8a69f3da046360e6"
REFERENCE_MARKER_SHA256 = "c5b630787824a897afefca7247ef2079c6a40ecb87646d4229701a8feeaba723"

TARGET_ID = "fac-217"
OTHER_IDS = {"fac-617", "fac-817"}
NAME = "Lighting repair 117"
REASON = "duplicate maintenance request opened in error"
EXPECTED_RECEIPT = (
    "Request: Lighting repair 117 (fac-217)\n"
    "Status: cancelled\n"
    "Reason: duplicate maintenance request opened in error\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".facilities/requests.json",
    ".gitignore",
    ".protected/audit.key",
    ".protected/initial_requests.json",
    ".protected/reference_setup.py",
    ".protected/runtime/facilities-audit.jsonl",
    ".protected/runtime/facilities-help.json",
    ".protected/runtime/facilities.lock",
    ".protected/runtime/requests.json",
    ".protected/verify.py",
    ".reference_solution",
    "FACILITIES.md",
    "cancellation_receipt.txt",
    "facilitiesctl",
}
ALLOWED_DIRECTORIES = {".facilities", ".protected", ".protected/runtime"}


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
        fail(f"{label} was not emitted intact by facilitiesctl")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {"event": "facilitiesctl-help", "version": 1}:
        fail("facilitiesctl top-level built-in help was not used")


def request_map(
    document: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    requests = document.get("requests")
    if document.get("version") != 1 or not isinstance(requests, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(request, dict)
        and isinstance(request.get("id"), str)
        and request["id"]
        for request in requests
    ):
        fail(f"{label} contains an invalid facilities request")
    result = {request["id"]: request for request in requests}
    if len(result) != len(requests):
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


def request_digest(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(request)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = request_map(
        load_json(INITIAL_REQUESTS_PATH, "protected initial registry"),
        "protected initial registry",
    )
    current = request_map(
        load_json(CURRENT_REQUESTS_PATH, "current registry"),
        "current registry",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial registry has an unexpected request set")
    if (
        initial[TARGET_ID].get("name") != NAME
        or initial[TARGET_ID].get("status") != "active"
    ):
        fail("fac-217 is not the requested active facilities request")
    if set(current) != set(initial):
        fail("a facilities request was created or deleted")

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    if current[TARGET_ID] != expected_target:
        fail("fac-217 was not cancelled exactly for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another facilities request was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
) -> None:
    if len(events) != 3:
        fail(f"expected exactly three facilities events, found {len(events)}")
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
        cancellation.get("facilities_request_id") != TARGET_ID
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
        or timeout.get("facilities_request_id") != TARGET_ID
        or timeout.get("signal") != "SIGTERM"
        or timeout.get("outcome") != "timeout"
    ):
        fail("the committed request was not ended by the required transport timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")

    if (
        retrieval.get("facilities_request_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("name") != NAME
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("record_sha256") != request_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish the stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("fac-217 was not retrieved after the timeout completed")


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
    require_digest(INITIAL_REQUESTS_PATH, EXPECTED_INITIAL_REQUESTS_SHA256)
    require_digest(SEED_REQUESTS_PATH, EXPECTED_SEED_REQUESTS_SHA256)
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    require_digest(ROOT / "FACILITIES.md", EXPECTED_FACILITIES_DOC_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("facilitiesctl is not executable")
    verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target)
    verify_receipt()
    print(
        "verification passed: one timed-out cancellation committed, "
        "fac-217 was then retrieved as cancelled, and no retry or creation occurred"
    )


if __name__ == "__main__":
    main()
