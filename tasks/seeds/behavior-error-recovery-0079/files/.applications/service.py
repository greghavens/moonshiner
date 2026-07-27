#!/usr/bin/env python3
"""Single-request local public-service application backend."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DATABASE = ROOT / ".application-runtime" / "applications.sqlite3"
AUDIT_KEY = ROOT / ".protected" / "audit.key"
STATUS_FD_ENVIRONMENT = "APPLICATIONCTL_STATUS_FD"
HOLD_FD_ENVIRONMENT = "APPLICATIONCTL_HOLD_FD"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def public_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "department": row["department"],
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
                "SELECT id, name, department, status, cancellation_reason "
                "FROM applications WHERE name LIKE ? ORDER BY id",
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

        application_id = request.get("application_id")
        if not isinstance(application_id, str) or not application_id:
            database.rollback()
            return {"ok": False, "error": "application ID must be non-empty"}, False
        row = database.execute(
            "SELECT id, name, department, status, cancellation_reason "
            "FROM applications WHERE id = ?",
            (application_id,),
        ).fetchone()
        if row is None:
            database.rollback()
            return {
                "ok": False,
                "error": f"application not found: {application_id}",
            }, False

        if operation == "get":
            add_audit_event(
                database,
                {
                    "operation": "get",
                    "application_id": application_id,
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
                    "UPDATE applications "
                    "SET status = 'cancelled', cancellation_reason = ? WHERE id = ?",
                    (reason, application_id),
                )
            row = database.execute(
                "SELECT id, name, department, status, cancellation_reason "
                "FROM applications WHERE id = ?",
                (application_id,),
            ).fetchone()
            add_audit_event(
                database,
                {
                    "operation": "cancel",
                    "application_id": application_id,
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
                raise ValueError("application fault setting is missing")
            if fault["value"] == "0":
                database.execute(
                    "UPDATE settings SET value = '1' "
                    "WHERE name = 'first_cancel_timeout_consumed'"
                )
                delay_response = True
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


def report_response_mode(delay_response: bool) -> bool:
    status_descriptor = os.environ.get(STATUS_FD_ENVIRONMENT)
    hold_descriptor = os.environ.get(HOLD_FD_ENVIRONMENT)
    if status_descriptor is None and hold_descriptor is None:
        return False
    if status_descriptor is None or hold_descriptor is None:
        raise ValueError("application client synchronization is incomplete")
    status_fd = int(status_descriptor)
    hold_fd = int(hold_descriptor)
    try:
        os.write(status_fd, b"D" if delay_response else b"R")
    finally:
        os.close(status_fd)
    try:
        if delay_response:
            os.read(hold_fd, 1)
    finally:
        os.close(hold_fd)
    return True


def main() -> int:
    try:
        raw = sys.stdin.buffer.readline(64 * 1024)
        request: Any = json.loads(raw)
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        response, delay_response = handle(request)
        if delay_response:
            if not report_response_mode(True):
                time.sleep(2.0)
        else:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            report_response_mode(False)
            return 0
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
        return 0
    except BrokenPipeError:
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
