#!/usr/bin/env python3
"""Deterministic protected verifier for behavior-error-recovery-0127."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED_ITEMS_PATH = ROOT / ".inventory" / "items.json"
RUNTIME_DIR = ROOT / ".protected" / "runtime"
CURRENT_ITEMS_PATH = RUNTIME_DIR / "items.json"
INITIAL_ITEMS_PATH = ROOT / ".protected" / "initial_items.json"
AUDIT_PATH = RUNTIME_DIR / "inventory-audit.jsonl"
HELP_PATH = RUNTIME_DIR / "inventory-help.json"
KEY_PATH = ROOT / ".protected" / "audit.key"
EXECUTABLE_PATH = ROOT / "inventoryctl"
RECEIPT_PATH = ROOT / "cancellation_receipt.txt"

EXPECTED_EXECUTABLE_SHA256 = "cd7cc6e9c718520e0754786bf779b7b86074653fbfa9faf230fcb85b55ef5ab9"
EXPECTED_INITIAL_ITEMS_SHA256 = "4c42d301767a2a7a80c803e0526cba136f2c7b5186594087718f4e335c468e69"
EXPECTED_SEED_ITEMS_SHA256 = "4c42d301767a2a7a80c803e0526cba136f2c7b5186594087718f4e335c468e69"
EXPECTED_REFERENCE_SETUP_SHA256 = "ddff98368252ec3ddcc05f89f42659158194ddf584644156f5febd1b508230ee"
EXPECTED_AUDIT_KEY_SHA256 = "a73cbbd04ffee3e63ce9331460ccc6e7179041f7ebd1a20cc6a4173d2ed5970e"
EXPECTED_GITIGNORE_SHA256 = "62c2065fac08c6768973bdac46eb0f2605a767094c1aa74b1cebb2abb2f7327e"
EXPECTED_INVENTORY_DOC_SHA256 = "30690365d0f06d4ca736a91b7d542851f1ba34057ff9a9ced572e0a4acd75488"
REFERENCE_MARKER_SHA256 = "532a3876582000e07fa2fbd3adb6fd699ea8def93c10e178ac9515902bfe4dd3"

TARGET_ID = "inv-227"
OTHER_IDS = {"inv-127", "inv-627"}
NAME = "Packing tape 127"
REASON = "duplicate stock record approved for retirement"
EXPECTED_RECEIPT = (
    "Inventory item: Packing tape 127 (inv-227)\n"
    "Verified status: cancelled\n"
    "Reason: duplicate stock record approved for retirement\n"
    "Retry performed: no\n"
)
ALLOWED_FILES = {
    ".gitignore",
    ".inventory/items.json",
    ".protected/audit.key",
    ".protected/initial_items.json",
    ".protected/reference_setup.py",
    ".protected/runtime/inventory-audit.jsonl",
    ".protected/runtime/inventory-help.json",
    ".protected/runtime/inventory.lock",
    ".protected/runtime/items.json",
    ".protected/verify.py",
    ".reference_solution",
    "cancellation_receipt.txt",
    "inventoryctl",
    "INVENTORY.md",
}
ALLOWED_DIRECTORIES = {".inventory", ".protected", ".protected/runtime"}


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
        fail(f"{label} was not emitted intact by inventoryctl")
    return unsigned


def verify_help_discovery() -> None:
    value = validate_seal(
        load_json(HELP_PATH, "built-in help attestation"),
        "built-in help attestation",
    )
    if value != {"event": "inventoryctl-help", "version": 1}:
        fail("inventoryctl top-level built-in help was not used")


def item_map(
    document: dict[str, Any],
    label: str,
) -> dict[str, dict[str, Any]]:
    items = document.get("items")
    if document.get("version") != 1 or not isinstance(items, list):
        fail(f"{label} has an invalid shape")
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"]
        for item in items
    ):
        fail(f"{label} contains an invalid inventory item")
    result = {item["id"]: item for item in items}
    if len(result) != len(items):
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


def item_digest(item: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(item)).hexdigest()


def verify_state() -> dict[str, Any]:
    initial = item_map(
        load_json(INITIAL_ITEMS_PATH, "protected initial inventory"),
        "protected initial inventory",
    )
    current = item_map(
        load_json(CURRENT_ITEMS_PATH, "current inventory"),
        "current inventory",
    )
    if set(initial) != {TARGET_ID, *OTHER_IDS}:
        fail("protected initial inventory has an unexpected item set")
    if (
        initial[TARGET_ID].get("name") != NAME
        or initial[TARGET_ID].get("status") != "active"
    ):
        fail("inv-227 is not the requested active inventory item")
    if set(current) != set(initial):
        fail("an inventory item was created or deleted")

    expected_target = dict(initial[TARGET_ID])
    expected_target["status"] = "cancelled"
    expected_target["cancellation_reason"] = REASON
    revision = expected_target.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        fail("inv-227 has an invalid initial revision")
    expected_target["revision"] = revision + 1
    if current[TARGET_ID] != expected_target:
        fail("inv-227 was not cancelled exactly once for the requested reason")
    for stable_id in OTHER_IDS:
        if current[stable_id] != initial[stable_id]:
            fail(f"another inventory item was modified: {stable_id}")
    return expected_target


def verify_execution(
    events: list[dict[str, Any]],
    expected_target: dict[str, Any],
) -> None:
    if len(events) != 3:
        fail(f"expected exactly three inventory events, found {len(events)}")
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
        cancellation.get("item_id") != TARGET_ID
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
        or timeout.get("item_id") != TARGET_ID
        or timeout.get("signal") != "SIGTERM"
        or timeout.get("outcome") != "timeout"
    ):
        fail("the committed request was not ended by the required transport timeout")
    observed = require_int(timeout, "observed_ns", "timeout event")
    if observed <= committed:
        fail("timeout was not observed after the uncertain commit")
    deadline_elapsed = observed - cancel_started
    if deadline_elapsed < 500_000_000 or deadline_elapsed > 2_500_000_000:
        fail("cancellation did not use the documented one-second deadline")

    if (
        retrieval.get("item_id") != TARGET_ID
        or retrieval.get("found") is not True
        or retrieval.get("outcome") != "ok"
        or retrieval.get("name") != NAME
        or retrieval.get("status") != "cancelled"
        or retrieval.get("cancellation_reason") != REASON
        or retrieval.get("record_sha256") != item_digest(expected_target)
    ):
        fail("post-timeout retrieval did not establish the stored cancellation")
    get_started = require_int(retrieval, "started_ns", "retrieval event")
    get_finished = require_int(retrieval, "finished_ns", "retrieval event")
    if get_started <= observed or get_started >= get_finished:
        fail("inv-227 was not retrieved after the timeout completed")


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
    require_digest(INITIAL_ITEMS_PATH, EXPECTED_INITIAL_ITEMS_SHA256)
    require_digest(SEED_ITEMS_PATH, EXPECTED_SEED_ITEMS_SHA256)
    require_digest(
        ROOT / ".protected" / "reference_setup.py",
        EXPECTED_REFERENCE_SETUP_SHA256,
    )
    require_digest(KEY_PATH, EXPECTED_AUDIT_KEY_SHA256)
    require_digest(ROOT / ".gitignore", EXPECTED_GITIGNORE_SHA256)
    require_digest(ROOT / "INVENTORY.md", EXPECTED_INVENTORY_DOC_SHA256)
    if not EXECUTABLE_PATH.stat().st_mode & 0o111:
        fail("inventoryctl is not executable")
    verify_help_discovery()
    expected_target = verify_state()
    verify_execution(load_audit(), expected_target)
    verify_receipt()
    print(
        "verification passed: one timed-out cancellation committed, inv-227 "
        "was then retrieved as cancelled, and no retry or creation occurred"
    )


if __name__ == "__main__":
    main()
