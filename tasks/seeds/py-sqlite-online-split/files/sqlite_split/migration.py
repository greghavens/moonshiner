"""Transactional migration from ``legacy_orders`` to normalized tables."""

from __future__ import annotations

import sqlite3


_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE
            CHECK (email = lower(trim(email)) AND email <> ''),
        display_name TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY,
        sku TEXT NOT NULL UNIQUE
            CHECK (sku = upper(trim(sku)) AND sku <> ''),
        name TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        FOREIGN KEY (customer_id) REFERENCES customers(id),
        FOREIGN KEY (product_id) REFERENCES products(id)
    )
    """,
)


_CUSTOMER_BACKFILL_SQL = """
    INSERT INTO customers (email, display_name)
    SELECT email_key, display_name
    FROM (
        SELECT
            lower(trim(customer_email)) AS email_key,
            trim(customer_name) AS display_name,
            row_number() OVER (
                PARTITION BY lower(trim(customer_email))
                ORDER BY id ASC
            ) AS preferred
        FROM legacy_orders
    )
    WHERE preferred = 1
    ON CONFLICT(email) DO UPDATE SET
        display_name = excluded.display_name
"""


_PRODUCT_BACKFILL_SQL = """
    INSERT INTO products (sku, name)
    SELECT sku_key, product_name
    FROM (
        SELECT
            upper(trim(product_sku)) AS sku_key,
            trim(product_name) AS product_name,
            row_number() OVER (
                PARTITION BY upper(trim(product_sku))
                ORDER BY id ASC
            ) AS preferred
        FROM legacy_orders
    )
    WHERE preferred = 1
    ON CONFLICT(sku) DO UPDATE SET
        name = excluded.name
"""


_ORDER_BACKFILL_SQL = """
    INSERT INTO orders (id, customer_id, product_id, quantity)
    SELECT legacy.id, customers.id, products.id, legacy.quantity
    FROM legacy_orders AS legacy
    JOIN customers
      ON customers.email = lower(trim(legacy.customer_email))
    JOIN products
      ON products.sku = upper(trim(legacy.product_sku))
    WHERE 1
    ON CONFLICT(id) DO UPDATE SET
        customer_id = excluded.customer_id,
        product_id = excluded.product_id,
        quantity = excluded.quantity
"""


REVERSE_VALIDATION_SQL = """
    WITH legacy_projection AS (
        SELECT
            legacy.id AS order_id,
            lower(trim(legacy.customer_email)) AS customer_email,
            (
                SELECT trim(candidate.customer_name)
                FROM legacy_orders AS candidate
                WHERE lower(trim(candidate.customer_email)) =
                      lower(trim(legacy.customer_email))
                ORDER BY candidate.id DESC
                LIMIT 1
            ) AS customer_name,
            upper(trim(legacy.product_sku)) AS product_sku,
            (
                SELECT trim(candidate.product_name)
                FROM legacy_orders AS candidate
                WHERE upper(trim(candidate.product_sku)) =
                      upper(trim(legacy.product_sku))
                ORDER BY candidate.id DESC
                LIMIT 1
            ) AS product_name,
            legacy.quantity
        FROM legacy_orders AS legacy
    ),
    normalized_projection AS (
        SELECT
            orders.id AS order_id,
            customers.email AS customer_email,
            customers.display_name AS customer_name,
            products.sku AS product_sku,
            products.name AS product_name,
            orders.quantity
        FROM orders
        JOIN customers ON customers.id = orders.customer_id
        JOIN products ON products.id = orders.product_id
    ),
    missing_or_changed AS (
        SELECT 'missing_or_changed' AS difference, * FROM legacy_projection
        EXCEPT
        SELECT 'missing_or_changed' AS difference, * FROM normalized_projection
    ),
    unexpected AS (
        SELECT 'unexpected' AS difference, * FROM normalized_projection
        EXCEPT
        SELECT 'unexpected' AS difference, * FROM legacy_projection
    )
    SELECT * FROM missing_or_changed
    UNION ALL
    SELECT * FROM unexpected
    ORDER BY order_id, difference
"""


def _enable_foreign_keys(connection: sqlite3.Connection) -> None:
    """Enable FK enforcement, or reject a transaction where it cannot be enabled."""

    if connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        return
    if connection.in_transaction:
        raise sqlite3.OperationalError(
            "foreign keys must be enabled before starting the surrounding transaction"
        )
    connection.execute("PRAGMA foreign_keys = ON")
    if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise sqlite3.OperationalError("could not enable SQLite foreign keys")


def split_legacy_orders(connection: sqlite3.Connection) -> None:
    """Create and backfill the normalized order tables atomically.

    A savepoint makes the operation safe both as a top-level migration and when
    the caller already owns a transaction. Releasing a top-level savepoint
    commits the migration; releasing a nested one leaves the outer transaction
    under the caller's control.
    """

    _enable_foreign_keys(connection)
    connection.execute("SAVEPOINT split_legacy_orders")
    try:
        for statement in _SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.execute(_CUSTOMER_BACKFILL_SQL)
        connection.execute(_PRODUCT_BACKFILL_SQL)
        connection.execute(_ORDER_BACKFILL_SQL)
    except Exception:
        connection.execute("ROLLBACK TO SAVEPOINT split_legacy_orders")
        connection.execute("RELEASE SAVEPOINT split_legacy_orders")
        raise
    else:
        connection.execute("RELEASE SAVEPOINT split_legacy_orders")
