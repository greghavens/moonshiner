"""SQLite expand/backfill/contract operations for structured customer names."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .repository import compose_name, split_legacy_name

MIGRATION_NAME = "customer_structured_names"


@dataclass(frozen=True)
class BatchResult:
    scanned: int
    migrated: int
    checkpoint: int
    complete: bool


def connect_memory() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def create_legacy_schema(connection: sqlite3.Connection) -> None:
    with connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS name_migration_state (
                migration_name TEXT PRIMARY KEY,
                phase TEXT NOT NULL CHECK (phase IN ('legacy', 'expanded', 'contracted')),
                last_customer_id INTEGER NOT NULL DEFAULT 0 CHECK (last_customer_id >= 0),
                backfill_complete INTEGER NOT NULL DEFAULT 0 CHECK (backfill_complete IN (0, 1))
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO name_migration_state(
                migration_name, phase, last_customer_id, backfill_complete
            ) VALUES (?, 'legacy', 0, 0)
            """,
            (MIGRATION_NAME,),
        )


def migration_status(connection: sqlite3.Connection) -> Mapping[str, Any]:
    row = connection.execute(
        """
        SELECT phase, last_customer_id, backfill_complete
        FROM name_migration_state WHERE migration_name = ?
        """,
        (MIGRATION_NAME,),
    ).fetchone()
    if row is None:
        raise RuntimeError("legacy schema has not been initialized")
    return {
        "phase": row["phase"],
        "last_customer_id": row["last_customer_id"],
        "backfill_complete": bool(row["backfill_complete"]),
    }


def load_legacy_fixture(connection: sqlite3.Connection, fixture: Mapping[str, Any]) -> None:
    create_legacy_schema(connection)
    customers = fixture.get("customers")
    if not isinstance(customers, list):
        raise ValueError("fixture customers must be a list")
    with connection:
        for customer in customers:
            connection.execute(
                "INSERT INTO customers(id, name, email) VALUES (?, ?, ?)",
                (customer["id"], customer["name"], customer["email"]),
            )


def expand_schema(connection: sqlite3.Connection) -> bool:
    status = migration_status(connection)
    if status["phase"] in {"expanded", "contracted"}:
        return False
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(customers)")
    }
    with connection:
        if "given_name" not in columns:
            connection.execute("ALTER TABLE customers ADD COLUMN given_name TEXT")
        if "family_name" not in columns:
            connection.execute("ALTER TABLE customers ADD COLUMN family_name TEXT")
        connection.execute(
            """
            UPDATE name_migration_state
            SET phase = 'expanded', last_customer_id = 0, backfill_complete = 0
            WHERE migration_name = ?
            """,
            (MIGRATION_NAME,),
        )
    return True


def backfill_batch(
    connection: sqlite3.Connection,
    batch_size: int,
    *,
    after_customer: Optional[Callable[[int], None]] = None,
) -> BatchResult:
    """Backfill one ascending-ID batch and persist a resumable checkpoint."""
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    status = migration_status(connection)
    if status["phase"] != "expanded":
        raise RuntimeError("backfill requires the expanded schema")

    rows = connection.execute(
        """
        SELECT id, name, given_name, family_name
        FROM customers
        WHERE id > ?
        ORDER BY id
        LIMIT ?
        """,
        (status["last_customer_id"], batch_size),
    ).fetchall()
    if not rows:
        with connection:
            connection.execute(
                """
                UPDATE name_migration_state SET backfill_complete = 1
                WHERE migration_name = ?
                """,
                (MIGRATION_NAME,),
            )
        return BatchResult(0, 0, status["last_customer_id"], True)

    checkpoint = rows[-1]["id"]
    has_more = connection.execute(
        "SELECT 1 FROM customers WHERE id > ? LIMIT 1", (checkpoint,)
    ).fetchone() is not None

    # Defect: progress is committed before the matching customer writes.
    connection.execute(
        """
        UPDATE name_migration_state
        SET last_customer_id = ?, backfill_complete = ?
        WHERE migration_name = ?
        """,
        (checkpoint, int(not has_more), MIGRATION_NAME),
    )
    connection.commit()

    migrated = 0
    try:
        connection.execute("BEGIN")
        for row in rows:
            if row["given_name"] is None or row["family_name"] is None:
                given, family = split_legacy_name(row["name"])
                connection.execute(
                    """
                    UPDATE customers SET given_name = ?, family_name = ?
                    WHERE id = ?
                    """,
                    (given, family, row["id"]),
                )
                migrated += 1
            if after_customer is not None:
                after_customer(row["id"])
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

    return BatchResult(len(rows), migrated, checkpoint, not has_more)


def contract_schema(connection: sqlite3.Connection) -> bool:
    status = migration_status(connection)
    if status["phase"] == "contracted":
        return False
    if status["phase"] != "expanded" or not status["backfill_complete"]:
        raise RuntimeError("contract requires a completed backfill")
    missing = connection.execute(
        """
        SELECT COUNT(*) FROM customers
        WHERE given_name IS NULL OR family_name IS NULL
        """
    ).fetchone()[0]
    if missing:
        raise RuntimeError("contract requires structured names for every customer")

    with connection:
        connection.execute(
            """
            CREATE TABLE customers_contracted (
                id INTEGER PRIMARY KEY,
                given_name TEXT NOT NULL,
                family_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE
            )
            """
        )
        connection.execute(
            """
            INSERT INTO customers_contracted(id, given_name, family_name, email)
            SELECT id, given_name, family_name, email FROM customers
            """
        )
        connection.execute("DROP TABLE customers")
        connection.execute("ALTER TABLE customers_contracted RENAME TO customers")
        connection.execute(
            """
            UPDATE name_migration_state SET phase = 'contracted'
            WHERE migration_name = ?
            """,
            (MIGRATION_NAME,),
        )
    return True


def capture_rollback_fixture(connection: sqlite3.Connection) -> Mapping[str, Any]:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(customers)")
    }
    selected = "id, email, name" if "name" in columns else "id, email, given_name, family_name"
    rows = connection.execute(
        f"SELECT {selected} FROM customers ORDER BY id"
    ).fetchall()
    customers = []
    for row in rows:
        legacy_name = (
            row["name"]
            if "name" in columns
            else compose_name(row["given_name"], row["family_name"])
        )
        customers.append(
            {"id": row["id"], "name": legacy_name, "email": row["email"]}
        )
    return {
        "fixture_version": 1,
        "migration": MIGRATION_NAME,
        "customers": customers,
    }


def rollback_to_legacy(
    connection: sqlite3.Connection, fixture: Mapping[str, Any]
) -> None:
    if fixture.get("fixture_version") != 1 or fixture.get("migration") != MIGRATION_NAME:
        raise ValueError("unsupported rollback fixture")
    customers = fixture.get("customers")
    if not isinstance(customers, list):
        raise ValueError("rollback fixture customers must be a list")

    try:
        connection.execute("BEGIN")
        connection.execute("DROP TABLE customers")
        connection.execute(
            """
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE
            )
            """
        )
        for customer in customers:
            connection.execute(
                "INSERT INTO customers(id, name, email) VALUES (?, ?, ?)",
                (customer["id"], customer["name"], customer["email"]),
            )
        connection.execute(
            """
            UPDATE name_migration_state
            SET phase = 'legacy', last_customer_id = 0, backfill_complete = 0
            WHERE migration_name = ?
            """,
            (MIGRATION_NAME,),
        )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
