#!/usr/bin/env python3
"""Protected deterministic verification for the native archive lookup task."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / ".archive" / "seed.sql"
DATABASE = ROOT / ".archive" / "runtime.sqlite3"
OUTPUT = ROOT / "archive_record.json"
RECEIPT_DOMAIN = b"moonshiner-request-archive-receipt-v1"


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def snapshot(database: sqlite3.Connection) -> tuple[list[dict[str, object]], str]:
    rows = database.execute(
        """
        SELECT id, location, name, status
        FROM archived_requests
        ORDER BY id
        """
    ).fetchall()
    records = [dict(row) for row in rows]
    digest = hashlib.sha256(
        canonical_json(records).encode("utf-8")
    ).hexdigest()
    return records, digest


def canonical_state() -> tuple[list[dict[str, object]], dict[str, object], str]:
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    try:
        database.executescript(SEED.read_text(encoding="utf-8"))
        records, digest = snapshot(database)
    finally:
        database.close()
    matches = [record for record in records if record["id"] == "fac-117"]
    require(len(matches) == 1, "protected archive source lacks the requested stable ID")
    return records, matches[0], digest


def expected_output(record: dict[str, object]) -> str:
    access = "read-only"
    body = canonical_json({"access": access, "record": record}).encode("utf-8")
    receipt = hashlib.sha256(RECEIPT_DOMAIN + b"\0" + body).hexdigest()
    payload = {"access": access, "receipt": receipt, "record": record}
    return canonical_json(payload) + "\n"


def verify_artifact(record: dict[str, object]) -> None:
    require(OUTPUT.is_file(), "archive_record.json is missing")
    require(
        OUTPUT.read_text(encoding="utf-8") == expected_output(record),
        "archive_record.json is not the executable's exact native returned record",
    )


def verify_state(
    canonical_records: list[dict[str, object]],
    canonical_digest: str,
) -> None:
    require(DATABASE.is_file(), "request archive environment is not initialized")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        current_records, current_digest = snapshot(database)
        require(
            current_records == canonical_records and current_digest == canonical_digest,
            "archived request state changed during the read-only lookup",
        )
        audit = database.execute(
            "SELECT * FROM operation_audit ORDER BY sequence"
        ).fetchall()
    finally:
        database.close()

    require(len(audit) == 1, "expected exactly one archive executable operation")
    row = audit[0]
    require(row["operation"] == "get", "the archive operation was not get")
    require(row["error"] is None, "the archive operation failed")
    arguments = json.loads(row["arguments_json"])
    require(
        arguments
        == {
            "access": "read-only",
            "id": "fac-117",
            "output": "archive_record.json",
        },
        "the archive operation was not grounded in the requested fields",
    )
    require(
        row["access_mode"] == "read-only"
        and row["output_path"] == "archive_record.json",
        "the lookup did not use the requested access or output mode",
    )
    require(
        row["result_count"] == 1 and row["result_id"] == "fac-117",
        "the lookup did not retrieve exactly the requested stable ID",
    )
    require(
        row["records_before_hash"] == canonical_digest
        and row["records_after_hash"] == canonical_digest,
        "the executable did not preserve read-only archive state",
    )


def verify_scope() -> None:
    repository = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if Path(repository).resolve() != ROOT.resolve():
        # Seed-authoring workspaces contain files/ below their own repository;
        # materialized task workspaces make files/ contents the repository root.
        return
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    observed = {line[3:] for line in status if len(line) >= 4}
    allowed = {"archive_record.json", "reference_driver.py"}
    require(observed <= allowed, "unexpected workspace artifacts were created")
    require("archive_record.json" in observed, "lookup artifact is not a deliverable")


def main() -> int:
    try:
        canonical_records, record, canonical_digest = canonical_state()
        verify_artifact(record)
        verify_state(canonical_records, canonical_digest)
        verify_scope()
    except (
        VerificationError,
        OSError,
        sqlite3.Error,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact native lookup artifact and read-only archive state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
