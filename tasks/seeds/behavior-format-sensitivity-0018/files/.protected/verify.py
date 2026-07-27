#!/usr/bin/env python3
"""Deterministic protected verification for the strict Python-call lookup task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
DATA = PROTECTED / "subscriptions.json"
KEY = PROTECTED / "audit.key"
SETUP = PROTECTED / "setup.py"
CLIENT = ROOT / "telecom-console"
GITIGNORE = ROOT / ".gitignore"
REFERENCE_STATE = ROOT / ".reference-state"
RUNTIME = ROOT / ".telecom-runtime"
AUDIT = RUNTIME / "audit.jsonl"

EXPECTED_HASHES = {
    ".gitignore": "238dc1c52de985a38ad9da370d7838e4ed068c22bcdbbc899c46e1e297956e3c",
    ".protected/subscriptions.json": "0823c9dd465d43178d88c8b2777cdb8e55aaa58475041f49e85f82157c74c196",
    ".protected/audit.key": "5a25dd786886bcc3f9bed98af8dc0917b7eba31730ee5acb957c41e4c4214ac6",
    ".protected/setup.py": "04837413d583414456a18f829517483269651911cc8424cb6d9ca7ea95805b50",
    "telecom-console": "817ed885cb1679e4370e53d84899928bc3f8738cce94af85dd5015e9304cec01",
}
EXPECTED_REFERENCE_STATE_HASHES = {
    "4b654bd1437066b13498661f3ca14774daf1066d072036beffaf06f0c014250e",
    "4151674fad2310eaff3e54db63b6ee84a6c96a68dd46c0f8df5df620d57f899a",
}
ALLOWED_ROOT_ENTRIES = {
    ".git",
    ".gitignore",
    ".protected",
    ".reference-state",
    ".telecom-runtime",
    "telecom-console",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "setup.py",
    "subscriptions.json",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "audit.jsonl",
    "audit.lock",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read {path.name}: {error}")


def verify_supplied_files() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            fail(f"supplied file changed: {relative}")
    if (
        not REFERENCE_STATE.is_file()
        or sha256(REFERENCE_STATE) not in EXPECTED_REFERENCE_STATE_HASHES
    ):
        fail("reference validation state is invalid")
    if not PROTECTED.is_dir():
        fail("protected directory is missing")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("protected file inventory changed")


def verify_layout() -> None:
    unexpected = sorted(
        path.name for path in ROOT.iterdir() if path.name not in ALLOWED_ROOT_ENTRIES
    )
    if unexpected:
        fail(f"unexpected sandbox artifact: {unexpected[0]}")
    if not RUNTIME.is_dir():
        fail("the genuine telecom console executable was not run")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("runtime evidence inventory is invalid")


def load_catalog() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        document = json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"subscription data is invalid: {error}")
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        fail("subscription data has an invalid shape")
    target_id = document.get("verification_target")
    targets = [
        record
        for record in document["records"]
        if isinstance(record, dict) and record.get("stable_id") == target_id
    ]
    if len(targets) != 1:
        fail("subscription data does not identify one audit target")
    target = targets[0]
    if not isinstance(target.get("stable_id"), str) or not target["stable_id"]:
        fail("the audit target has an invalid stable ID")
    return document, target


def load_event() -> dict[str, Any]:
    if not AUDIT.is_file():
        fail("signed executable evidence is missing")
    try:
        lines = [
            line
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        events = [json.loads(line) for line in lines]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"runtime evidence is invalid: {error}")
    if len(events) != 1 or not isinstance(events[0], dict):
        fail("exactly one telecom console operation is required")
    signed = events[0]
    event = dict(signed)
    signature = event.pop("signature", None)
    try:
        key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        fail(f"signing material is invalid: {error}")
    expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        fail("runtime evidence was not emitted intact by telecom-console")
    return event


def verify_event(event: dict[str, Any], record: dict[str, Any]) -> None:
    stable_id = record["stable_id"]
    expression = f"telecom_get(id={json.dumps(stable_id, ensure_ascii=False)})"
    expected_event = {
        "access": "read-only",
        "catalog_sha256": sha256(DATA),
        "operation": "telecom_get",
        "request": {
            "expression": expression,
            "id": stable_id,
        },
        "reference_state_sha256": sha256(REFERENCE_STATE),
        "result": {
            "found": True,
            "record_sha256": hashlib.sha256(canonical(record)).hexdigest(),
        },
        "sequence": 1,
    }
    if event != expected_event:
        fail("execution was not exactly one grounded read-only Python-call lookup")


def main() -> int:
    verify_supplied_files()
    verify_layout()
    _document, record = load_catalog()
    event = load_event()
    verify_event(event, record)
    print("verification passed: one exact grounded read-only telecom lookup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
