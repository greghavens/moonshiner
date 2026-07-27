#!/usr/bin/env python3
"""SQLite-backed local service with transaction-bound signed evidence."""

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
KEY_PATH = ROOT / ".protected" / "audit.key"
TARGET_ID = "lib-193"
TIMEOUT_SETTING = "first_target_cancel_timeout_consumed"


def canonical(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def seal(payload: str) -> str:
    key = KEY_PATH.read_bytes().strip()
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def title_record(database: sqlite3.Connection, title_id: str) -> dict[str, Any]:
    row = database.execute(
        "SELECT id, title, collection_name AS collection, scheduled_date, "
        "status, cancellation_reason FROM library_titles WHERE id = ?",
        (title_id,),
    ).fetchone()
    if row is None:
        raise ValueError("library title not found")
    return dict(row)


def append_event(database: sqlite3.Connection, event: dict[str, Any]) -> None:
    sequence = database.execute(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM audit_events"
    ).fetchone()[0]
    complete_event = {"sequence": sequence, **event}
    payload = canonical(complete_event)
    database.execute(
        "INSERT INTO audit_events(sequence, payload, seal) VALUES (?, ?, ?)",
        (sequence, payload, seal(payload)),
    )


def get_title(database: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    title_id = request.get("title_id")
    if not isinstance(title_id, str) or not title_id:
        raise ValueError("a stable title ID is required")
    database.execute("BEGIN IMMEDIATE")
    record = title_record(database, title_id)
    append_event(
        database,
        {
            "operation": "get",
            "title_id": title_id,
            "observed_status": record["status"],
        },
    )
    database.commit()
    return record


def cancel_title(
    database: sqlite3.Connection, request: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    title_id = request.get("title_id")
    reason = request.get("reason")
    if not isinstance(title_id, str) or not title_id:
        raise ValueError("a stable title ID is required")
    if not isinstance(reason, str) or not reason:
        raise ValueError("a cancellation reason is required")

    database.execute("BEGIN IMMEDIATE")
    before = title_record(database, title_id)
    timeout_after_commit = False
    if before["status"] == "active":
        database.execute(
            "UPDATE library_titles SET status = 'cancellation-pending', "
            "cancellation_reason = ? WHERE id = ?",
            (reason, title_id),
        )
    after = title_record(database, title_id)

    if title_id == TARGET_ID:
        setting = database.execute(
            "SELECT value FROM settings WHERE name = ?", (TIMEOUT_SETTING,)
        ).fetchone()
        if setting is not None and setting[0] == "0":
            timeout_after_commit = True
            database.execute(
                "UPDATE settings SET value = '1' WHERE name = ?", (TIMEOUT_SETTING,)
            )

    append_event(
        database,
        {
            "operation": "cancel",
            "title_id": title_id,
            "reason": reason,
            "status_before": before["status"],
            "status_after": after["status"],
            "outcome": "response_deadline" if timeout_after_commit else "returned",
        },
    )
    database.commit()
    return after, timeout_after_commit


def search_titles(database: sqlite3.Connection, request: dict[str, Any]) -> dict[str, Any]:
    query = request.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("a search query is required")
    database.execute("BEGIN IMMEDIATE")
    rows = database.execute(
        "SELECT id FROM library_titles WHERE title LIKE ? ORDER BY id",
        (f"%{query}%",),
    ).fetchall()
    matched_ids = [row[0] for row in rows]
    append_event(
        database,
        {"operation": "search", "query": query, "matched_ids": matched_ids},
    )
    database.commit()
    return {"query": query, "matched_ids": matched_ids}


def main() -> int:
    try:
        request: Any = json.loads(sys.stdin.readline())
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        database = sqlite3.connect(DATABASE, timeout=5)
        database.row_factory = sqlite3.Row
        try:
            operation = request.get("operation")
            timeout_after_commit = False
            if operation == "get":
                record = get_title(database, request)
            elif operation == "cancel":
                record, timeout_after_commit = cancel_title(database, request)
            elif operation == "search":
                record = search_titles(database, request)
            else:
                raise ValueError("unsupported library operation")
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

        if timeout_after_commit:
            time.sleep(2.0)
        print(canonical({"ok": True, "record": record}), flush=True)
        return 0
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
