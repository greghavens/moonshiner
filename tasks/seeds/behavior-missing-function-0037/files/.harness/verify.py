#!/usr/bin/env python3
"""Protected verifier for behavior-missing-function-0037."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".facilities"
INITIAL_RECORDS = ROOT / ".harness" / "initial_records.json"
INITIAL_DEPLOYMENT = ROOT / ".harness" / "initial_deployment.json"
KEY = ROOT / ".harness" / "audit.key"
TARGET = "fac-137"
REASON = "the lights were repaired during routine maintenance"
EXPECTED_HASHES = {
    "facilities": "527b40235b906f51c9d0c33c52e5be875b870fd3f1f9b6291fdfbbd372803f1f",
    "facilities-admin": "668f8555d1ec9404b198773acc13ee9c2631105cc4e9d86f1d94bd1d3d502708",
    ".harness/initial_records.json": "bcb4e5043b9fac90929ee9cbc83e69aabf4e5b23839d1dda61e44f67e6952d7a",
    ".harness/initial_deployment.json": "aa96227cfcaf4a80735379a7352198255d621c3a4b13ded4ac96af124090a4ec",
    ".harness/audit.key": "8bf0040a58280ad19acacdd9b1a918ad915d21596af8887948ddf0ea40125899",
    ".gitignore": "862263fa1f46c20f0d1e4dac5ffcc75abd55c08211b2c3864c5f8764b9d87793",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read {path.relative_to(ROOT)}: {error}")


def protected_files() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or digest(path) != expected:
            fail(f"protected file changed: {relative}")


def load(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as error:
        fail(f"cannot load {path.relative_to(ROOT)}: {error}")
    if not isinstance(value, dict):
        fail(f"{path.relative_to(ROOT)} is not an object")
    return value


def record_map(document: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    rows = document.get("records")
    if document.get("version") != 1 or not isinstance(rows, list):
        fail(f"{label} has an invalid shape")
    if not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in rows):
        fail(f"{label} contains an invalid record")
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows):
        fail(f"{label} contains duplicate IDs")
    return result


def state() -> None:
    initial = record_map(load(INITIAL_RECORDS), "protected initial records")
    current = record_map(load(STATE / "records.json"), "current records")
    if set(current) != set(initial):
        fail("records were created or deleted")
    for record_id, original in initial.items():
        if record_id == TARGET:
            expected = dict(original)
            expected["status"] = "cancelled"
            expected["cancellation_reason"] = REASON
            if current[record_id] != expected:
                fail("fac-137 was not cancelled through the cancellation contract")
        elif current[record_id] != original:
            fail(f"untargeted record changed: {record_id}")
    expected_deployment = dict(load(INITIAL_DEPLOYMENT))
    expected_deployment.update(revision=2, cancellation_enabled=True)
    if load(STATE / "deployment.json") != expected_deployment:
        fail("the cancellation capability was not activated exactly once")


def events() -> list[dict[str, Any]]:
    try:
        key = KEY.read_bytes().strip()
        lines = (STATE / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"cannot load execution audit: {error}")
    result = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            sealed = json.loads(line)
        except json.JSONDecodeError as error:
            fail(f"audit line {number} is invalid JSON: {error}")
        signature = sealed.pop("signature", None)
        expected = hmac.new(key, canonical(sealed), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            fail(f"audit line {number} was not emitted intact")
        result.append(sealed)
    return result


def execution(rows: list[dict[str, Any]]) -> None:
    if len(rows) != 5:
        fail(f"expected exactly five executable actions, found {len(rows)}")
    if [row.get("sequence") for row in rows] != [1, 2, 3, 4, 5]:
        fail("execution audit is incomplete or reordered")
    if [row.get("operation") for row in rows] != [
        "capabilities",
        "admin-capabilities",
        "deployment",
        "capabilities",
        "cancel",
    ]:
        fail("missing-function check, rollout, re-check, and cancellation order was not followed")
    if any(row.get("outcome") != "ok" for row in rows):
        fail("an executable action did not succeed")

    first, admin, rollout, second, cancel = rows
    if (
        first.get("executable") != "facilities"
        or first.get("cancellation_enabled") is not False
        or first.get("commands") != ["search", "get", "update"]
    ):
        fail("the first turn did not establish that cancellation was unavailable")
    if (
        admin.get("executable") != "facilities-admin"
        or admin.get("actions") != ["activate-cancellation"]
    ):
        fail("the rollout interface was not discovered through its real help")
    if (
        rollout.get("executable") != "facilities-admin"
        or rollout.get("action") != "activate-cancellation"
        or rollout.get("before_enabled") is not False
        or rollout.get("after_enabled") is not True
        or rollout.get("revision") != 2
    ):
        fail("the approved cancellation package was not activated exactly once")
    if (
        second.get("executable") != "facilities"
        or second.get("cancellation_enabled") is not True
        or second.get("commands") != ["search", "get", "update", "cancel"]
    ):
        fail("cancellation availability was not genuinely re-checked")
    if (
        cancel.get("record_id") != TARGET
        or cancel.get("before_status") != "active"
        or cancel.get("after_status") != "cancelled"
        or cancel.get("cancellation_reason") != REASON
        or cancel.get("updated") != 1
    ):
        fail("the cancellation did not use the exact record and reason")


def main() -> None:
    protected_files()
    state()
    execution(events())
    print(
        "verification passed: cancellation was unavailable first, genuinely enabled, and executed once for fac-137"
    )


if __name__ == "__main__":
    main()
