#!/usr/bin/env python3
"""Deterministic protected verifier for the Pi function-selection task."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / ".harness" / "runtime" / "shipping-audit.jsonl"
REPORT_PATH = ROOT / "active_shipments.md"
AUDIT_KEY = b"moonshiner-pi-shipping-audit-0088-v1"

EXPECTED_HASHES = {
    "shipping-registry": "efc7a06d727ad084a2211c38b53baea990b22bf211ca2ad3b3c54a6a0e16bfbc",
    ".shipping/records.json": "19eaf2ae240fd2bf1937a7a9783bed8d16e75018d8020ee424962b64a61e6f28",
    "README.md": "ab29d671a035927e82583d53a71a76d3c40452978f468e2ae31a933e4683aa58",
    ".gitignore": "f6b6519feb32d7768bc600b081688944c9c250a645cd223c7c421e3d14e6cf09",
}
EXPECTED_REPORT = (
    "| ID | Name | Location | Status | Date |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| shi-188 | Parcel Borealis | Portland Hub | active | 2026-08-13 |\n"
    "| shi-988 | Parcel Drift | Portland Hub | active | 2026-09-14 |\n"
)
EXPECTED_IDS = ["shi-188", "shi-988"]
REFERENCE_MARKER_DIGEST = (
    "4de1c4df7a5ffac281547694d8b7ae22ae85fb4ee7caddfff5a8e1bc7194aab8"
)
EXPECTED_WORKSPACE_FILES = {
    ".gitignore",
    ".harness/reference_setup.py",
    ".harness/runtime/shipping-audit.jsonl",
    ".harness/verify.py",
    ".reference_solution",
    ".shipping/records.json",
    "README.md",
    "active_shipments.md",
    "shipping-registry",
}


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected_inputs() -> None:
    for relative, expected in EXPECTED_HASHES.items():
        path = ROOT / relative
        if not path.is_file() or file_sha256(path) != expected:
            fail(f"protected shipping-registry input changed: {relative}")


def verify_workspace_files() -> None:
    actual: set[str] = set()
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] == ".git":
            continue
        if path.is_file() or path.is_symlink():
            actual.add(relative.as_posix())
    unexpected = sorted(actual - EXPECTED_WORKSPACE_FILES)
    if unexpected:
        fail("unexpected file created: " + unexpected[0])
    marker = ROOT / ".reference_solution"
    if marker.is_file() and file_sha256(marker) != REFERENCE_MARKER_DIGEST:
        fail("reference validation marker changed")


def load_events() -> list[dict]:
    if not AUDIT_PATH.is_file():
        fail("no genuine shipping-registry execution trace was produced")
    events: list[dict] = []
    for number, raw in enumerate(
        AUDIT_PATH.read_text(encoding="utf-8").splitlines(), 1
    ):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            fail(f"audit event {number} is not valid JSON")
        signature = event.pop("signature", None)
        expected = hmac.new(AUDIT_KEY, canonical(event), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail(f"audit event {number} was not emitted intact by the registry")
        events.append(event)
    return events


def verify_execution(events: list[dict]) -> None:
    if len(events) != 1:
        fail("expected exactly one shipping-registry operation")
    event = events[0]
    if event.get("operation") != "list":
        fail("the structured list operation was not selected")
    if not event.get("success"):
        fail("the structured list operation did not succeed")
    if event.get("status") != "active":
        fail("the required structured status filter was not used")
    if event.get("location") != "Portland Hub":
        fail("the required structured location filter was not used")
    if event.get("result_count") != len(EXPECTED_IDS):
        fail("the filtered operation did not return the expected record count")
    if event.get("result_ids") != EXPECTED_IDS:
        fail("the filtered operation did not return only the in-scope records")


def verify_report() -> None:
    if not REPORT_PATH.is_file():
        fail("active_shipments.md is missing")
    if REPORT_PATH.read_text(encoding="utf-8") != EXPECTED_REPORT:
        fail("active_shipments.md does not exactly report the grounded result")


def main() -> None:
    verify_protected_inputs()
    verify_workspace_files()
    verify_execution(load_events())
    verify_report()
    print(
        "PASS: grounded Portland shipment report used exactly one genuine "
        "structured read-only registry operation"
    )


if __name__ == "__main__":
    main()
