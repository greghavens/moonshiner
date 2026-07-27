#!/usr/bin/env python3
"""Protected verification for paired checks and selective error recovery."""

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
RUNTIME = ROOT / ".campaign-runtime"
DATABASE = RUNTIME / "campaigns.sqlite3"
AUDIT = RUNTIME / "audit.jsonl"
DELIVERABLE = ROOT / "availability-report.md"
SEED = PROTECTED / "campaign_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "campaign-availability"
DOCUMENTATION = ROOT / "CAMPAIGNS.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
TARGETS = (
    {
        "name": "Museum donor thank-you",
        "audience": "Donors",
        "date": "2026-09-02",
    },
    {
        "name": "Fall enrollment notice",
        "audience": "Students",
        "date": "2026-09-02",
    },
)
DATA_TABLES = ("campaigns", "availability", "notifications")
EXPECTED_CLIENT_SHA256 = "629e9ed989343d0e5d31bc38730750e56b79fe2a075ff470b09d1dc0b3f18847"
EXPECTED_SEED_SHA256 = "b5d07a729026bfa6e93d2ffcdd8e87be2329639589932ab34187316024cd069a"
EXPECTED_KEY_SHA256 = "25b16f1e020d43fd07593cbe065c9d3ee0c19587db1c6686de9e8d7e33255721"
EXPECTED_DOCUMENTATION_SHA256 = "708915e967f6e127b86916e62011edb9ac87099f6430ef5b41f6cb8fe0037b06"
EXPECTED_SETUP_SHA256 = "d7c23439689be68a57b0b525eb8d697f79bbc77e452310eed35963000a76e81e"
EXPECTED_GITIGNORE_SHA256 = "3cf7956e615e7278b406087a8d5cb11dbc3459f8c56d170c8f5f56fd71195172"
ALLOWED_ROOT_ENTRIES = {
    ".campaign-runtime",
    ".git",
    ".gitignore",
    ".protected",
    ".reference_solution.py",
    "CAMPAIGNS.md",
    "availability-report.md",
    "campaign-availability",
}
EXPECTED_PROTECTED_ENTRIES = {
    "audit.key",
    "campaign_seed.sql",
    "setup.py",
    "verify.py",
}
EXPECTED_RUNTIME_ENTRIES = {
    "attempts.json",
    "attempts.lock",
    "audit.jsonl",
    "campaigns.sqlite3",
    "initialize.lock",
}


def fail(message: str) -> None:
    print(f"verification failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        fail(f"cannot read protected sandbox input {path.name}: {error}")


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_connection() -> sqlite3.Connection:
    protected_files = (
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine campaign executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected campaign seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected signing material"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "campaign documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected reference setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(SEED.read_text(encoding="utf-8"))
    except (OSError, sqlite3.Error) as error:
        fail(f"cannot load protected campaign seed: {error}")
    return connection


def rows(connection: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return sorted(tuple(row) for row in connection.execute(f"SELECT * FROM {table}"))


def schema(connection: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
    ]


def expected_results(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    results: list[dict[str, Any]] = []
    for target in TARGETS:
        row = connection.execute(
            "SELECT campaign_name AS name, audience, availability_date AS date, "
            "available FROM availability WHERE campaign_name = ? AND audience = ? "
            "AND availability_date = ?",
            (target["name"], target["audience"], target["date"]),
        ).fetchone()
        if row is None:
            fail("the protected seed lacks a requested availability record")
        result = dict(row)
        result["available"] = bool(result["available"])
        results.append(result)
    return results


def verify_deliverable(results: list[dict[str, Any]]) -> None:
    if not DELIVERABLE.is_file():
        fail("availability-report.md is missing")
    lines = []
    for result in results:
        availability_text = "available" if result["available"] else "unavailable"
        lines.append(
            f"- {result['name']} in {result['audience']}: "
            f"{availability_text} on {result['date']}."
        )
    lines.append(
        f"- Retried branch: {results[0]['name']} in {results[0]['audience']}."
    )
    expected = "\n".join(lines) + "\n"
    try:
        actual = DELIVERABLE.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read availability-report.md as UTF-8: {error}")
    if actual != expected:
        fail("availability-report.md is not the exact grounded recovery report")
    if len(actual.splitlines()) != 3 or any(
        not line.startswith("- ") for line in actual.splitlines()
    ):
        fail("availability-report.md is not exactly three Markdown bullets")
    forbidden = (
        "campaign_service_busy",
        "operation_failed",
        "retryable",
        "transient",
        "cam-",
        "preview",
        "review",
        "Members",
        "Faculty",
    )
    if any(value in actual for value in forbidden):
        fail("availability-report.md contains raw errors or out-of-scope records")


def verify_read_only(canonical: sqlite3.Connection) -> None:
    if not DATABASE.is_file():
        fail("the genuine campaign executable was not run")
    actual = sqlite3.connect(DATABASE)
    try:
        if actual.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            fail("the campaign database failed its integrity check")
        if schema(actual) != schema(canonical):
            fail("the campaign database schema changed")
        for table in DATA_TABLES:
            if rows(actual, table) != rows(canonical, table):
                fail(f"read-only campaign state changed in table {table}")
    finally:
        actual.close()


def verify_layout() -> None:
    root_entries = {path.name for path in ROOT.iterdir()}
    unexpected = sorted(root_entries - ALLOWED_ROOT_ENTRIES)
    if unexpected:
        fail(f"unexpected scratch artifact remains: {unexpected[0]}")
    if {path.name for path in PROTECTED.iterdir()} != EXPECTED_PROTECTED_ENTRIES:
        fail("the protected file inventory changed")
    if not RUNTIME.is_dir():
        fail("the genuine campaign runtime directory is missing")
    if {path.name for path in RUNTIME.iterdir()} != EXPECTED_RUNTIME_ENTRIES:
        fail("the campaign runtime has missing or unexpected artifacts")


def load_audit() -> list[dict[str, Any]]:
    if not AUDIT.is_file():
        fail("no signed campaign-executable evidence was recorded")
    try:
        entries = [
            json.loads(line)
            for line in AUDIT.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        fail(f"campaign operation evidence is invalid: {error}")
    if len(entries) != 3:
        fail("expected exactly two initial checks and one selective retry")
    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    verified: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            fail("a campaign operation evidence entry is not an object")
        unsigned = dict(entry)
        signature = unsigned.pop("signature", None)
        expected = hmac.new(key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("campaign operation evidence was not emitted intact")
        verified.append(unsigned)
    return verified


def intervals_overlap(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return max(first["started_ns"], second["started_ns"]) < min(
        first["finished_ns"], second["finished_ns"]
    )


def verify_trace(entries: list[dict[str, Any]], results: list[dict[str, Any]]) -> None:
    if any(entry.get("operation") != "availability" for entry in entries):
        fail("a get, create, schedule, update, notify, or other operation was used")
    for entry in entries:
        for field in ("attempt", "started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int) or isinstance(
                entry.get(field), bool
            ):
                fail(f"signed execution field {field} is missing or invalid")
        if entry["started_ns"] >= entry["finished_ns"]:
            fail("an availability operation has an invalid execution interval")

    branches = []
    for target in TARGETS:
        branch = [entry for entry in entries if entry.get("arguments") == target]
        branches.append(sorted(branch, key=lambda value: value["started_ns"]))
    if len(branches[0]) != 2 or len(branches[1]) != 1:
        fail(
            "the successful branch was repeated, an extra record was checked, "
            "or the failed branch was not retried once"
        )

    initial_failure, retry = branches[0]
    retained_success = branches[1][0]
    if retained_success.get("success") is not True or retained_success["attempt"] != 1:
        fail("the retained branch lacks one valid initial success")
    if retained_success.get("result_digest") != digest(results[1]):
        fail("the retained successful availability response is incorrect")

    if initial_failure.get("success") is not False or initial_failure["attempt"] != 1:
        fail("the retry branch does not begin with exactly one failed check")
    if (
        initial_failure.get("error_code") != "campaign_service_busy"
        or initial_failure.get("retryable") is not True
        or initial_failure.get("transient") is not True
    ):
        fail("the failed initial branch was not explicitly transient and retryable")

    if retry.get("success") is not True or retry["attempt"] != 2:
        fail("the failed branch was not successfully retried exactly once")
    if retry.get("arguments") != initial_failure.get("arguments"):
        fail("the retry changed a campaign name, audience, or date")
    if retry.get("result_digest") != digest(results[0]):
        fail("the retry's successful availability response is incorrect")

    if not intervals_overlap(initial_failure, retained_success):
        fail("the two independent initial checks were not issued together")
    if retry["started_ns"] <= max(
        initial_failure["finished_ns"], retained_success["finished_ns"]
    ):
        fail("the retry began before both initial branches returned")


def main() -> int:
    canonical = canonical_connection()
    try:
        results = expected_results(canonical)
        verify_deliverable(results)
        verify_read_only(canonical)
        verify_trace(load_audit(), results)
        verify_layout()
    finally:
        canonical.close()
    print(
        "verification passed: genuine paired checks, retained success, "
        "one selective retry, strict scope, and grounded report verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
