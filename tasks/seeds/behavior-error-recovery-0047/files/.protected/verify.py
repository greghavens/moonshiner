#!/usr/bin/env python3
"""Protected verifier for uncertain inventory cancellation recovery."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent.parent
PROTECTED = ROOT / ".protected"
RUNTIME = ROOT / ".inventory-runtime"
DATABASE = RUNTIME / "inventory.sqlite3"
JOURNAL = RUNTIME / "operations.jsonl"
SEED = PROTECTED / "inventory_seed.sql"
KEY = PROTECTED / "audit.key"
CLIENT = ROOT / "inventory-service"
DOCUMENTATION = ROOT / "INVENTORY.md"
SETUP = PROTECTED / "setup.py"
GITIGNORE = ROOT / ".gitignore"
REPORT = ROOT / "cancellation-report.txt"
TARGET_ID = "inv-147"
REASON = "scheduled work is no longer needed"
EXPECTED_REPORT = (
    "inv-147: cancelled; verified status: active; cancellation retried: yes\n"
)
EXPECTED_CLIENT_SHA256 = "49864a7ba7792dedf2bf8dfb5597b8a29893a38c4e6ee70a6cd676d57f17c062"
EXPECTED_SEED_SHA256 = "a127e6afbcf162dc0d04df078e18ba7b7ba1a4b899df2d8c91097d2c3e4fcb77"
EXPECTED_KEY_SHA256 = "d90d13935a80a0b71e3b72dd2d9160a988bfa1bf7297a5fb78f4a298cad011db"
EXPECTED_DOCUMENTATION_SHA256 = "34d95461c7804f2f5921625cfe2c45d7557012bafd7d46f17fbac51ee3edce20"
EXPECTED_SETUP_SHA256 = "d97f54f55b925163f221859a90538f8a7bb4e67d89c1ad4f433b93e515b1f7e9"
EXPECTED_GITIGNORE_SHA256 = "7d0f93f249dc0d771124e8180f867e1a0b2f57b3d67243a5aa4f5ad61a7ece3a"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def protected_baseline() -> sqlite3.Connection:
    protected_files = (
        (CLIENT, EXPECTED_CLIENT_SHA256, "genuine inventory executable"),
        (SEED, EXPECTED_SEED_SHA256, "protected inventory seed"),
        (KEY, EXPECTED_KEY_SHA256, "protected evidence-signing key"),
        (DOCUMENTATION, EXPECTED_DOCUMENTATION_SHA256, "operator documentation"),
        (SETUP, EXPECTED_SETUP_SHA256, "protected setup"),
        (GITIGNORE, EXPECTED_GITIGNORE_SHA256, "sandbox ignore rules"),
    )
    for path, expected, label in protected_files:
        if not path.is_file() or sha256(path) != expected:
            fail(f"{label} changed")

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SEED.read_text(encoding="utf-8"))
    return connection


def item_rows(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, object]]:
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, name, location, status, cancellation_reason, revision "
        "FROM items ORDER BY id"
    ).fetchall()
    return {str(row["id"]): dict(row) for row in rows}


def expected_state(
    baseline: sqlite3.Connection,
) -> tuple[
    dict[str, dict[str, object]], dict[str, object], dict[str, object]
]:
    baseline_rows = item_rows(baseline)
    if TARGET_ID not in baseline_rows:
        fail("protected seed does not contain the target item")
    original_target = baseline_rows[TARGET_ID]
    if original_target["status"] != "active":
        fail("protected target does not begin active")
    duplicate_names = [
        item_id
        for item_id, record in baseline_rows.items()
        if item_id != TARGET_ID and record["name"] == original_target["name"]
    ]
    if not duplicate_names:
        fail("protected seed does not exercise immutable-ID selection")
    cancelled_target = {
        **original_target,
        "status": "cancelled",
        "cancellation_reason": REASON,
        "revision": int(original_target["revision"]) + 1,
    }
    return baseline_rows, original_target, cancelled_target


def verify_database(
    baseline_rows: dict[str, dict[str, object]],
    cancelled_target: dict[str, object],
) -> None:
    if not DATABASE.is_file():
        fail("the genuine inventory executable was not run")
    actual: sqlite3.Connection | None = None
    try:
        actual = sqlite3.connect(DATABASE)
        actual.row_factory = sqlite3.Row
        actual_rows = item_rows(actual)
        attempts = [
            tuple(row)
            for row in actual.execute(
                "SELECT item_id, attempt_count FROM cancellation_attempts "
                "ORDER BY item_id"
            ).fetchall()
        ]
        notifications = [
            tuple(row)
            for row in actual.execute(
                "SELECT notification_id, item_id, message FROM notifications "
                "ORDER BY notification_id"
            ).fetchall()
        ]
        tables = {
            str(row[0])
            for row in actual.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    except (OSError, sqlite3.DatabaseError) as error:
        fail(f"cannot read the runtime database: {error}")
    finally:
        if actual is not None:
            actual.close()

    expected_tables = {
        "items",
        "cancellation_attempts",
        "notifications",
        "sqlite_sequence",
    }
    if tables != expected_tables:
        fail("the runtime database schema changed")
    if set(actual_rows) != set(baseline_rows):
        fail("the set of inventory item IDs changed")
    for item_id, baseline_record in baseline_rows.items():
        if item_id != TARGET_ID and actual_rows[item_id] != baseline_record:
            fail(f"non-target item {item_id} was changed")
    if actual_rows[TARGET_ID] != cancelled_target:
        fail("inv-147 is not cancelled with the exact reason and one revision change")
    if attempts != [(TARGET_ID, 2)]:
        fail("the required two cancellation submissions were not made exactly")
    if notifications:
        fail("a forbidden notification was created")


def load_journal() -> list[dict[str, object]]:
    if not JOURNAL.is_file():
        fail("no genuine inventory-executable evidence was recorded")
    try:
        signed_entries = [
            json.loads(line)
            for line in JOURNAL.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        fail(f"inventory-executable evidence is invalid: {error}")
    if len(signed_entries) != 3:
        fail("expected one cancellation, one fetch, and one conditional retry")

    key = bytes.fromhex(KEY.read_text(encoding="utf-8").strip())
    entries: list[dict[str, object]] = []
    for signed_entry in signed_entries:
        if not isinstance(signed_entry, dict):
            fail("inventory-executable evidence contains a malformed entry")
        entry = dict(signed_entry)
        signature = entry.pop("signature", None)
        expected = hmac.new(key, canonical_json(entry), hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected
        ):
            fail("inventory-executable evidence has an invalid signature")
        entries.append(entry)
    return entries


def verify_trace(
    entries: list[dict[str, object]],
    original_target: dict[str, object],
    cancelled_target: dict[str, object],
) -> None:
    first, read, retry = entries
    for entry in entries:
        for field in ("started_ns", "finished_ns", "pid", "parent_pid"):
            if not isinstance(entry.get(field), int):
                fail(f"execution-evidence field {field} is missing")
        if int(entry["started_ns"]) >= int(entry["finished_ns"]):
            fail("an operation has an invalid execution interval")

    expected_arguments = {"id": TARGET_ID, "reason": REASON}
    if first.get("operation") != "cancel" or first.get("arguments") != expected_arguments:
        fail("the first data operation was not the exact target cancellation")
    if first.get("attempt") != 1 or first.get("success") is not False:
        fail("the first cancellation did not record one uncertain attempt")
    if (
        first.get("outcome") != "response_lost_unknown"
        or first.get("error_code") != "response_lost"
        or first.get("reached_service") is not True
        or first.get("may_have_committed") is not True
        or first.get("business_commit") is not False
    ):
        fail("the required uncertain response-loss condition was not exercised")
    if int(first["finished_ns"]) - int(first["started_ns"]) < 250_000_000:
        fail("the first cancellation did not actually wait for its deadline")

    if read.get("operation") != "fetch" or read.get("arguments") != {"id": TARGET_ID}:
        fail("the uncertain cancellation was not followed by a direct ID fetch")
    if (
        read.get("success") is not True
        or read.get("outcome") != "record"
        or read.get("observed_status") != "active"
        or read.get("result_digest") != digest(original_target)
    ):
        fail("the direct fetch did not establish that inv-147 remained active")
    if int(read["started_ns"]) < int(first["finished_ns"]):
        fail("the direct fetch began before the uncertain cancellation returned")

    if retry.get("operation") != "cancel" or retry.get("arguments") != expected_arguments:
        fail("the active item did not trigger one identical cancellation retry")
    if (
        retry.get("attempt") != 2
        or retry.get("success") is not True
        or retry.get("outcome") != "cancelled"
        or retry.get("result_digest") != digest(cancelled_target)
    ):
        fail("the one permitted retry did not produce the required cancellation")
    if int(retry["started_ns"]) < int(read["finished_ns"]):
        fail("the retry began before the direct fetch finished")


def verify_report() -> None:
    if not REPORT.is_file():
        fail("cancellation-report.txt is missing")
    try:
        contents = REPORT.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        fail(f"cannot read cancellation-report.txt: {error}")
    if contents != EXPECTED_REPORT:
        fail("cancellation-report.txt does not report the grounded retry outcome")


def verify_workspace_shape() -> None:
    allowed = {
        ".git",
        ".gitignore",
        ".inventory-runtime",
        ".protected",
        ".reference_solution.py",
        ".sandbox-home",
        "INVENTORY.md",
        "cancellation-report.txt",
        "inventory-service",
    }
    unexpected = sorted(path.name for path in ROOT.iterdir() if path.name not in allowed)
    if unexpected:
        fail(f"unexpected sandbox artifacts remain: {', '.join(unexpected)}")
    if RUNTIME.is_dir():
        expected_runtime = {
            "initialize.lock",
            "inventory.sqlite3",
            "operations.jsonl",
        }
        actual_runtime = {path.name for path in RUNTIME.iterdir()}
        if actual_runtime != expected_runtime:
            fail("unexpected or missing generated inventory runtime artifact")


def main() -> int:
    baseline = protected_baseline()
    try:
        baseline_rows, original_target, cancelled_target = expected_state(baseline)
    finally:
        baseline.close()
    verify_workspace_shape()
    verify_database(baseline_rows, cancelled_target)
    verify_trace(load_journal(), original_target, cancelled_target)
    verify_report()
    print(
        "PASS: inv-147 was fetched after the uncertain response, then cancelled "
        "by exactly one necessary retry"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
