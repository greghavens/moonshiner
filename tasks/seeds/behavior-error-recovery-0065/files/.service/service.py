#!/usr/bin/env python3
"""Single-request local support service backed by SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".support-runtime" / "support.sqlite3"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
COMMITTED_WITHOUT_RESPONSE_MARKER = "__supportctl_committed_without_response__"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def public_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "queue": row["queue"],
        "status": row["status"],
        "cancellation_reason": row["cancellation_reason"],
    }


def add_audit_event(
    database: sqlite3.Connection, event: dict[str, Any]
) -> None:
    row = database.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM audit_events"
    ).fetchone()
    sequence = int(row[0])
    signed_event = {"sequence": sequence, **event}
    payload = canonical(signed_event).decode("utf-8")
    key = AUDIT_KEY.read_bytes().strip()
    seal = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    database.execute(
        "INSERT INTO audit_events(sequence, payload, seal) VALUES (?, ?, ?)",
        (sequence, payload, seal),
    )


def handle(request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    operation = request.get("operation")
    case_id = request.get("case_id")
    if not isinstance(case_id, str) or not case_id:
        return {"ok": False, "error": "case ID must be a non-empty string"}, False

    database = sqlite3.connect(DATABASE, timeout=5)
    database.row_factory = sqlite3.Row
    delay_response = False
    try:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT id, title, queue, status, cancellation_reason "
            "FROM cases WHERE id = ?",
            (case_id,),
        ).fetchone()
        if row is None:
            database.rollback()
            return {"ok": False, "error": f"case not found: {case_id}"}, False

        if operation == "get":
            add_audit_event(
                database,
                {
                    "operation": "get",
                    "case_id": case_id,
                    "observed_status": row["status"],
                },
            )
        elif operation == "cancel":
            reason = request.get("reason")
            if not isinstance(reason, str) or not reason:
                database.rollback()
                return {"ok": False, "error": "reason must be non-empty"}, False
            status_before = row["status"]
            if status_before == "active":
                database.execute(
                    "UPDATE cases SET status = 'cancelled', cancellation_reason = ? "
                    "WHERE id = ?",
                    (reason, case_id),
                )
            row = database.execute(
                "SELECT id, title, queue, status, cancellation_reason "
                "FROM cases WHERE id = ?",
                (case_id,),
            ).fetchone()
            add_audit_event(
                database,
                {
                    "operation": "cancel",
                    "case_id": case_id,
                    "reason": reason,
                    "status_before": status_before,
                    "status_after": row["status"],
                },
            )
            fault = database.execute(
                "SELECT value FROM settings "
                "WHERE name = 'first_cancel_timeout_consumed'"
            ).fetchone()
            if fault is None:
                raise ValueError("support fault setting is missing")
            if fault["value"] == "0":
                database.execute(
                    "UPDATE settings SET value = '1' "
                    "WHERE name = 'first_cancel_timeout_consumed'"
                )
                delay_response = True
        else:
            database.rollback()
            return {"ok": False, "error": f"unsupported operation: {operation}"}, False

        database.commit()
        response = {"ok": True, "record": public_record(row)}
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()
    return response, delay_response


def main() -> int:
    try:
        raw = sys.stdin.buffer.readline(64 * 1024)
        request: Any = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        response, delay_response = handle(request)
        if delay_response:
            print(COMMITTED_WITHOUT_RESPONSE_MARKER, flush=True)
            while True:
                time.sleep(3600)
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
