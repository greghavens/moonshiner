"""Nightly stock-feed importer for the warehouse ledger.

Batches are all-or-nothing: any row that violates the feed contract rejects
the entire batch and nothing from it may persist. The one tolerated per-row
condition is a SKU that already exists on hand — that row alone is skipped
and reported while the rest of the batch goes through. Connections are
long-lived and must stay usable after a rejected batch.
"""

import sqlite3


class BatchError(Exception):
    """A row violated the batch contract; the whole batch must be rejected."""

    def __init__(self, index, sku, reason):
        super().__init__(f"row {index} ({sku!r}): {reason}")
        self.index = index
        self.sku = sku
        self.reason = reason


def ensure_schema(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS stock ("
        " sku TEXT PRIMARY KEY,"
        " qty INTEGER NOT NULL,"
        " bin TEXT NOT NULL)"
    )


def _row_problem(row):
    if not row.get("sku"):
        return "missing sku"
    qty = row.get("qty")
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0:
        return "qty must be a non-negative integer"
    if not row.get("bin"):
        return "missing bin"
    return None


def import_batch(conn, rows):
    """Import one feed batch; returns {"inserted": n, "skipped": [skus]}."""
    inserted = 0
    skipped = []
    for index, row in enumerate(rows):
        problem = _row_problem(row)
        if problem is not None:
            conn.rollback()
            raise BatchError(index, row.get("sku"), problem)
        try:
            conn.execute(
                "INSERT INTO stock (sku, qty, bin) VALUES (?, ?, ?)",
                (row["sku"], row["qty"], row["bin"]),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            skipped.append(row["sku"])
            continue
        conn.commit()
        inserted += 1
    return {"inserted": inserted, "skipped": skipped}
