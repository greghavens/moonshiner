#!/usr/bin/env python3
"""Deterministic protected verification for the strict native-call task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
ARCHIVE = PROTECTED / "archive.json"
KEY = PROTECTED / "audit.key"
SETUP = PROTECTED / "setup.py"
CLIENT = ROOT / "trip-archive"
GITIGNORE = ROOT / ".gitignore"
REFERENCE_STATE = ROOT / ".reference-state"
RUNTIME = ROOT / ".trip-runtime"
AUDIT = RUNTIME / "audit.jsonl"
TARGET_ID = "tra-102"

EXPECTED_HASHES = {
    ".gitignore": "ed3745d2e214fbf137251284c610ab67efb43e8aecf0799424be1b8b889d7315",
    ".protected/archive.json": "acb677bfe3e9969694e949c677d4bbcba81715786628472f61c437a0096fd2c8",
    ".protected/audit.key": "22da6d50ee4de2aaef8448d6e4d5f63aa3847baf84a1d37418abf8af018b52f5",
    ".protected/setup.py": "a12908aa5c068f3c9dba02ee65efd23058e3b7bbe9d251d179842ea757ece91c",
    "trip-archive": "7f957e9eecc04b225a6b486fb0c7816d8bcd42b86364f422ef5c0f45df0d40fc",
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
    ".trip-runtime",
    "trip-archive",
}
EXPECTED_PROTECTED_ENTRIES = {
    "archive.json",
    "audit.key",
    "setup.py",
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
        fail("the genuine trip archive executable was not run")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("runtime evidence inventory is invalid")


def load_archive() -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        document = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"archive data is invalid: {error}")
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        fail("archive data has an invalid shape")
    matches = [
        record
        for record in document["records"]
        if isinstance(record, dict) and record.get("stable_id") == TARGET_ID
    ]
    if len(matches) != 1:
        fail("protected archive does not contain one target record")
    return document, matches[0]


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
        fail("exactly one trip archive operation is required")
    signed = events[0]
    event = dict(signed)
    signature = event.pop("signature", None)
    try:
        key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as error:
        fail(f"signing material is invalid: {error}")
    expected = hmac.new(key, canonical(event), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        fail("runtime evidence was not emitted intact by trip-archive")
    return event


def verify_event(event: dict[str, Any], record: dict[str, Any]) -> None:
    expected_event = {
        "access": "read-only",
        "archive_sha256": sha256(ARCHIVE),
        "operation": "lookup",
        "request": {"stable_id": TARGET_ID},
        "reference_state_sha256": sha256(REFERENCE_STATE),
        "result": {
            "found": True,
            "record_sha256": hashlib.sha256(canonical(record)).hexdigest(),
        },
        "sequence": 1,
    }
    if event != expected_event:
        fail("the execution was not exactly one grounded read-only target lookup")


def main() -> int:
    verify_supplied_files()
    verify_layout()
    _document, record = load_archive()
    event = load_event()
    verify_event(event, record)
    print("verification passed: one grounded read-only trip lookup")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
