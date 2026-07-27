#!/usr/bin/env python3
"""Single-request local library service backed by SQLite."""

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
DATABASE = ROOT / ".library-runtime" / "library.sqlite3"
AUDIT_KEY = ROOT / ".protected" / "audit.key"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def public_record(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "collection": row["collection"],
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
                "SELECT id, title, collection, status, cancellation_reason "
                "FROM titles WHERE title LIKE ? ORDER BY id",
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

        title_id = request.get("title_id")
        if not isinstance(title_id, str) or not title_id:
            database.rollback()
            return {"ok": False, "error": "title ID must be a non-empty string"}, False
        row = database.execute(
            "SELECT id, title, collection, status, cancellation_reason "
            "FROM titles WHERE id = ?",
            (title_id,),
        ).fetchone()
        if row is None:
            database.rollback()
            return {"ok": False, "error": f"title not found: {title_id}"}, False

        if operation == "get":
            add_audit_event(
                database,
                {
                    "operation": "get",
                    "title_id": title_id,
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
                    "UPDATE titles "
                    "SET status = 'cancelled', cancellation_reason = ? WHERE id = ?",
                    (reason, title_id),
                )
            row = database.execute(
                "SELECT id, title, collection, status, cancellation_reason "
                "FROM titles WHERE id = ?",
                (title_id,),
            ).fetchone()
            add_audit_event(
                database,
                {
                    "operation": "cancel",
                    "title_id": title_id,
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
                raise ValueError("library fault setting is missing")
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
