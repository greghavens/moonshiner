import sqlite3
import unittest

from sqlite_split import REVERSE_VALIDATION_SQL, split_legacy_orders


LEGACY_SCHEMA = """
    CREATE TABLE legacy_orders (
        id INTEGER PRIMARY KEY,
        customer_email TEXT NOT NULL,
        customer_name TEXT NOT NULL,
        product_sku TEXT NOT NULL,
        product_name TEXT NOT NULL,
        quantity INTEGER NOT NULL
    )
"""


def make_connection(rows=()):
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(LEGACY_SCHEMA)
    connection.executemany(
        "INSERT INTO legacy_orders VALUES (?, ?, ?, ?, ?, ?)", rows
    )
    connection.commit()
    return connection


def snapshot(connection):
    return {
        "customers": connection.execute(
            "SELECT id, email, display_name FROM customers ORDER BY id"
        ).fetchall(),
        "products": connection.execute(
            "SELECT id, sku, name FROM products ORDER BY id"
        ).fetchall(),
        "orders": connection.execute(
            "SELECT id, customer_id, product_id, quantity FROM orders ORDER BY id"
        ).fetchall(),
    }


class SplitLegacyOrdersTests(unittest.TestCase):
    def test_backfill_normalizes_keys_and_enforces_foreign_keys(self):
        connection = make_connection(
            [
                (1, " Alice@Example.com ", " Alice ", " sku-1 ", " Anvil ", 2),
                (2, "bob@example.com", "Bob", "SKU-2", "Balloon", 3),
            ]
        )

        split_legacy_orders(connection)

        self.assertEqual(
            connection.execute(
                "SELECT email, display_name FROM customers ORDER BY email"
            ).fetchall(),
            [("alice@example.com", "Alice"), ("bob@example.com", "Bob")],
        )
        self.assertEqual(
            connection.execute("SELECT sku, name FROM products ORDER BY sku").fetchall(),
            [("SKU-1", "Anvil"), ("SKU-2", "Balloon")],
        )
        self.assertEqual(
            connection.execute("SELECT id, quantity FROM orders ORDER BY id").fetchall(),
            [(1, 2), (2, 3)],
        )
        targets = {
            row[2]
            for row in connection.execute("PRAGMA foreign_key_list(orders)")
        }
        self.assertEqual(targets, {"customers", "products"})
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO orders VALUES (99, 999, 999, 1)")

    def test_duplicate_dimensions_choose_the_greatest_legacy_id(self):
        connection = make_connection(
            [
                (10, "sam@example.com", "Old Sam", "part-7", "Old Part", 1),
                (30, " SAM@example.com ", "Canonical Sam", " PART-7 ", "New Part", 4),
                (20, "other@example.com", "Other", "part-8", "Other Part", 2),
            ]
        )

        split_legacy_orders(connection)

        self.assertEqual(
            connection.execute(
                "SELECT display_name FROM customers WHERE email = 'sam@example.com'"
            ).fetchone()[0],
            "Canonical Sam",
        )
        self.assertEqual(
            connection.execute(
                "SELECT name FROM products WHERE sku = 'PART-7'"
            ).fetchone()[0],
            "New Part",
        )
        self.assertEqual(connection.execute(REVERSE_VALIDATION_SQL).fetchall(), [])

    def test_rerun_is_idempotent_and_preserves_dimension_ids(self):
        connection = make_connection(
            [(1, "one@example.com", "One", "sku-1", "First", 1)]
        )
        split_legacy_orders(connection)
        customer_id = connection.execute(
            "SELECT id FROM customers WHERE email = 'one@example.com'"
        ).fetchone()[0]
        product_id = connection.execute(
            "SELECT id FROM products WHERE sku = 'SKU-1'"
        ).fetchone()[0]
        connection.execute(
            """
            UPDATE legacy_orders
            SET customer_name = 'One Updated',
                product_name = 'First Updated',
                quantity = 5
            WHERE id = 1
            """
        )

        split_legacy_orders(connection)

        self.assertEqual(
            connection.execute(
                "SELECT id, display_name FROM customers WHERE email = 'one@example.com'"
            ).fetchone(),
            (customer_id, "One Updated"),
        )
        self.assertEqual(
            connection.execute(
                "SELECT id, name FROM products WHERE sku = 'SKU-1'"
            ).fetchone(),
            (product_id, "First Updated"),
        )
        self.assertEqual(
            connection.execute(
                "SELECT customer_id, product_id, quantity FROM orders WHERE id = 1"
            ).fetchone(),
            (customer_id, product_id, 5),
        )
        after_update = snapshot(connection)
        split_legacy_orders(connection)
        self.assertEqual(snapshot(connection), after_update)

    def test_failed_backfill_rolls_back_schema_and_rows(self):
        connection = make_connection(
            [(1, "one@example.com", "One", "sku-1", "First", 1)]
        )
        split_legacy_orders(connection)
        before = snapshot(connection)
        connection.execute(
            "INSERT INTO legacy_orders VALUES (?, ?, ?, ?, ?, ?)",
            (2, "bad@example.com", "Bad", "sku-bad", "Bad", 0),
        )

        with self.assertRaises(sqlite3.IntegrityError):
            split_legacy_orders(connection)

        self.assertEqual(snapshot(connection), before)
        self.assertEqual(
            connection.execute(
                "SELECT count(*) FROM customers WHERE email = 'bad@example.com'"
            ).fetchone()[0],
            0,
        )
        self.assertEqual(
            connection.execute(
                "SELECT count(*) FROM products WHERE sku = 'SKU-BAD'"
            ).fetchone()[0],
            0,
        )

    def test_reverse_validation_query_detects_target_drift(self):
        connection = make_connection(
            [(1, "one@example.com", "One", "sku-1", "First", 2)]
        )
        split_legacy_orders(connection)
        self.assertEqual(connection.execute(REVERSE_VALIDATION_SQL).fetchall(), [])

        connection.execute("UPDATE products SET name = 'Drifted' WHERE sku = 'SKU-1'")

        differences = connection.execute(REVERSE_VALIDATION_SQL).fetchall()
        self.assertEqual({row[0] for row in differences}, {"missing_or_changed", "unexpected"})
        self.assertEqual({row[1] for row in differences}, {1})


if __name__ == "__main__":
    unittest.main()
