#!/usr/bin/env python3
"""Single-request local facilities operations backend."""

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
DATABASE = ROOT / ".facility-runtime" / "facilities.sqlite3"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
TARGET_ID = "fac-197"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def public_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "location": row["location"],
        "scheduled_date": row["scheduled_date"],
        "priority": row["priority"],
        "status": row["status"],
        "cancellation_reason": row["cancellation_reason"],
    }


def add_audit_event(database: sqlite3.Connection, event: dict[str, Any]) -> None:
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


def select_request(
    database: sqlite3.Connection, request_id: str
) -> sqlite3.Row | None:
    return database.execute(
        "SELECT id, name, location, scheduled_date, priority, status, "
        "cancellation_reason FROM requests WHERE id = ?",
        (request_id,),
    ).fetchone()


def handle(request: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    operation = request.get("operation")
    database = sqlite3.connect(DATABASE, timeout=5)
    database.row_factory = sqlite3.Row
    delay_response = False
    try:
        database.execute("BEGIN IMMEDIATE")

        if operation == "search":
            query = request.get("query")
            if not isinstance(query, str) or not query:
                database.rollback()
                return {"ok": False, "error": "query must be non-empty"}, False
            rows = database.execute(
                "SELECT id, name, location, scheduled_date, priority, status, "
                "cancellation_reason FROM requests "
                "WHERE name LIKE ? ORDER BY id",
                (f"%{query}%",),
            ).fetchall()
            add_audit_event(
                database,
                {
                    "operation": "search",
                    "query": query,
                    "matched_ids": [row["id"] for row in rows],
                },
            )
            database.commit()
            return {
                "ok": True,
                "record": {"matches": [public_record(row) for row in rows]},
            }, False

        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            database.rollback()
            return {"ok": False, "error": "request ID must be non-empty"}, False
        row = select_request(database, request_id)
        if row is None:
            database.rollback()
            return {
                "ok": False,
                "error": f"facilities request not found: {request_id}",
            }, False

        if operation == "get":
            add_audit_event(
                database,
                {
                    "operation": "get",
                    "request_id": request_id,
                    "observed_status": row["status"],
                },
            )
        elif operation == "cancel":
            reason = request.get("reason")
            if not isinstance(reason, str) or not reason:
                database.rollback()
                return {"ok": False, "error": "reason must be non-empty"}, False
            status_before = row["status"]
            outcome = "response_returned"
            fault = database.execute(
                "SELECT value FROM settings "
                "WHERE name = 'first_target_cancel_timeout_consumed'"
            ).fetchone()
            if fault is None:
                raise ValueError("facilities fault setting is missing")
            first_target_attempt = request_id == TARGET_ID and fault["value"] == "0"
            if first_target_attempt:
                database.execute(
                    "UPDATE settings SET value = '1' "
                    "WHERE name = 'first_target_cancel_timeout_consumed'"
                )
                database.execute(
                    "UPDATE requests SET status = 'cancelled', "
                    "cancellation_reason = ? WHERE id = ?",
                    (reason, request_id),
                )
                delay_response = True
                outcome = "response_deadline"
            elif status_before == "active":
                database.execute(
                    "UPDATE requests SET status = 'cancelled', "
                    "cancellation_reason = ? WHERE id = ?",
                    (reason, request_id),
                )
            row = select_request(database, request_id)
            if row is None:
                raise ValueError("facilities request disappeared during cancellation")
            add_audit_event(
                database,
                {
                    "operation": "cancel",
                    "request_id": request_id,
                    "reason": reason,
                    "status_before": status_before,
                    "status_after": row["status"],
                    "outcome": outcome,
                },
            )
        else:
            database.rollback()
            return {
                "ok": False,
                "error": f"unsupported operation: {operation}",
            }, False

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
            time.sleep(2.0)
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
