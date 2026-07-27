#!/usr/bin/env python3
"""Protected deterministic verification for the strict claim lookup task."""

from __future__ import annotations

import json
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REQUEST = ROOT / "queue" / "lookup.xml"
SOURCE = ROOT / "insurance.sql"
DATABASE = ROOT / "__pycache__" / "insurance.sqlite3"
OUTPUT = ROOT / "lookup-call.json"


class VerificationError(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def request_values() -> tuple[str, str]:
    root = ET.parse(REQUEST).getroot()
    require(root.tag == "lookup", "queue request must have one lookup root")
    require(
        [child.tag for child in root] == ["record-id", "mode"],
        "queue request must contain record-id followed by mode",
    )
    record_id = root[0].text or ""
    mode = root[1].text or ""
    require(record_id != "" and mode != "", "queue values must be nonempty")
    return record_id, mode


def canonical_state(
    record_id: str,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    source = sqlite3.connect(":memory:")
    try:
        source.executescript(SOURCE.read_text(encoding="utf-8"))
        rows = source.execute(
            "SELECT id FROM claims WHERE id = ?",
            (record_id,),
        ).fetchall()
        require(len(rows) == 1, "queued record must resolve to exactly one claim")
        claims = source.execute(
            """
            SELECT id, name, location, status, loss_date, adjuster, notes
              FROM claims
             ORDER BY id
            """
        ).fetchall()
        notifications = source.execute(
            "SELECT sequence, claim_id, message FROM notifications ORDER BY sequence"
        ).fetchall()
        return claims, notifications
    finally:
        source.close()


def expected_output(record_id: str) -> str:
    return (
        json.dumps(
            {
                "tool": "insurance_get",
                "arguments": {"id": record_id},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )


def verify_artifact(record_id: str) -> str:
    require(OUTPUT.is_file(), "lookup-call.json is missing")
    text = OUTPUT.read_text(encoding="utf-8")
    require(
        text == expected_output(record_id),
        "lookup-call.json is not the executable's exact compact tool-call object",
    )
    require(text.count("\n") == 1 and text.endswith("\n"), "output must be one line")
    parsed = json.loads(text)
    require(isinstance(parsed, dict), "output must be one JSON object")
    require(
        isinstance(parsed["arguments"]["id"], str),
        "record ID must remain a JSON string",
    )
    return text.rstrip("\n")


def verify_execution(
    record_id: str,
    mode: str,
    artifact: str,
    source_claims: list[tuple[object, ...]],
    source_notifications: list[tuple[object, ...]],
) -> None:
    require(DATABASE.is_file(), "the executable claim environment was not used")
    database = sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True)
    database.row_factory = sqlite3.Row
    try:
        rows = database.execute(
            "SELECT * FROM audit_log ORDER BY sequence"
        ).fetchall()
        require(len(rows) == 1, "expected exactly one claim-data operation")
        row = rows[0]
        require(row["operation"] == "get", "the sole operation must be get")
        require(row["violation"] == 0, "a forbidden claim operation was invoked")
        require(row["finished_ns"] is not None, "the get operation did not finish")
        require(row["error"] is None, "the get operation failed")
        require(
            json.loads(row["arguments_json"]) == {"id": record_id, "mode": mode},
            "get arguments were not grounded in both XML values",
        )
        require(
            row["result_count"] == 1 and row["returned_id"] == record_id,
            "get did not retrieve exactly the queued record",
        )
        require(
            row["output_json"] == artifact,
            "artifact was not captured verbatim from executable stdout",
        )

        claims = database.execute(
            """
            SELECT id, name, location, status, loss_date, adjuster, notes
              FROM claims
             ORDER BY id
            """
        ).fetchall()
        notifications = database.execute(
            "SELECT sequence, claim_id, message FROM notifications ORDER BY sequence"
        ).fetchall()
        require(
            [tuple(row) for row in claims] == source_claims,
            "claim state changed during the read-only lookup",
        )
        require(
            [tuple(row) for row in notifications] == source_notifications,
            "notification state changed during the read-only lookup",
        )
    finally:
        database.close()


def main() -> int:
    try:
        record_id, mode = request_values()
        source_claims, source_notifications = canonical_state(record_id)
        artifact = verify_artifact(record_id)
        verify_execution(
            record_id,
            mode,
            artifact,
            source_claims,
            source_notifications,
        )
    except (
        VerificationError,
        OSError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        sqlite3.Error,
        ET.ParseError,
    ) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: exact JSON, grounded get execution, and read-only state verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
