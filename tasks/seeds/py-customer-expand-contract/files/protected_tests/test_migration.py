import json
import sqlite3
import unittest
from pathlib import Path

from customer_names import (
    backfill_batch,
    capture_rollback_fixture,
    connect_memory,
    contract_schema,
    expand_schema,
    load_legacy_fixture,
    migration_status,
    rollback_to_legacy,
)


ROOT = Path(__file__).resolve().parents[1]


def fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_memory()
        load_legacy_fixture(self.connection, fixture("legacy_customers.json"))

    def tearDown(self):
        self.connection.close()

    def test_expand_backfill_contract_is_bounded_and_idempotent(self):
        self.assertTrue(expand_schema(self.connection))
        self.assertFalse(expand_schema(self.connection))

        first = backfill_batch(self.connection, 2)
        self.assertEqual((first.scanned, first.migrated, first.checkpoint, first.complete), (2, 2, 2, False))
        second = backfill_batch(self.connection, 2)
        self.assertEqual((second.scanned, second.migrated, second.checkpoint, second.complete), (2, 2, 7, True))
        repeated = backfill_batch(self.connection, 2)
        self.assertEqual((repeated.scanned, repeated.migrated, repeated.checkpoint, repeated.complete), (0, 0, 7, True))

        names = [
            tuple(row)
            for row in self.connection.execute(
                "SELECT given_name, family_name FROM customers ORDER BY id"
            )
        ]
        self.assertEqual(
            names,
            [("Ada", "Lovelace"), ("Prince", ""), ("Mary Jane", "Watson"), ("Grace", "Hopper")],
        )
        self.assertTrue(contract_schema(self.connection))
        self.assertFalse(contract_schema(self.connection))
        columns = [row["name"] for row in self.connection.execute("PRAGMA table_info(customers)")]
        self.assertNotIn("name", columns)
        self.assertEqual(migration_status(self.connection)["phase"], "contracted")

    def test_failed_batch_rolls_back_data_and_checkpoint_for_retry(self):
        expand_schema(self.connection)
        attempted_ids = []

        def fail_on_second(customer_id):
            attempted_ids.append(customer_id)
            if customer_id == 2:
                raise RuntimeError("deterministic interruption")

        with self.assertRaisesRegex(RuntimeError, "deterministic interruption"):
            backfill_batch(self.connection, 20, after_customer=fail_on_second)

        self.assertEqual(attempted_ids, [1, 2])
        status = migration_status(self.connection)
        self.assertEqual(status["last_customer_id"], 0)
        self.assertFalse(status["backfill_complete"])
        rows = self.connection.execute(
            "SELECT given_name, family_name FROM customers ORDER BY id"
        ).fetchall()
        self.assertTrue(all(row["given_name"] is None and row["family_name"] is None for row in rows))

        retried_ids = []
        retried = backfill_batch(
            self.connection, 20, after_customer=retried_ids.append
        )
        self.assertEqual(retried_ids, [1, 2, 3, 7])
        self.assertEqual(
            (retried.scanned, retried.migrated, retried.checkpoint, retried.complete),
            (4, 4, 7, True),
        )

    def test_checkpoint_write_failure_rolls_back_customer_updates(self):
        expand_schema(self.connection)
        with self.connection:
            self.connection.execute(
                """
                CREATE TRIGGER reject_name_checkpoint
                BEFORE UPDATE OF last_customer_id ON name_migration_state
                WHEN NEW.last_customer_id > 0
                BEGIN
                    SELECT RAISE(ABORT, 'checkpoint write failed');
                END
                """
            )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "checkpoint write failed"):
            backfill_batch(self.connection, 20)

        status = migration_status(self.connection)
        self.assertEqual(status["last_customer_id"], 0)
        self.assertFalse(status["backfill_complete"])
        rows = self.connection.execute(
            "SELECT given_name, family_name FROM customers ORDER BY id"
        ).fetchall()
        self.assertTrue(
            all(
                row["given_name"] is None and row["family_name"] is None
                for row in rows
            )
        )

    def test_existing_dual_written_rows_are_not_overwritten(self):
        expand_schema(self.connection)
        self.connection.execute(
            "UPDATE customers SET given_name = 'Augusta Ada', family_name = 'King' WHERE id = 1"
        )
        self.connection.commit()
        result = backfill_batch(self.connection, 4)
        self.assertEqual(result.migrated, 3)
        row = self.connection.execute(
            "SELECT given_name, family_name FROM customers WHERE id = 1"
        ).fetchone()
        self.assertEqual(tuple(row), ("Augusta Ada", "King"))

    def test_contract_rejects_incomplete_backfill(self):
        expand_schema(self.connection)
        backfill_batch(self.connection, 1)
        with self.assertRaisesRegex(RuntimeError, "completed backfill"):
            contract_schema(self.connection)

    def test_versioned_fixture_restores_exact_legacy_rows_after_contract(self):
        snapshot = fixture("rollback_snapshot.json")
        self.assertEqual(capture_rollback_fixture(self.connection), snapshot)
        expand_schema(self.connection)
        backfill_batch(self.connection, 20)
        contract_schema(self.connection)

        rollback_to_legacy(self.connection, snapshot)
        self.assertEqual(migration_status(self.connection)["phase"], "legacy")
        self.assertEqual(capture_rollback_fixture(self.connection), snapshot)
        columns = [row["name"] for row in self.connection.execute("PRAGMA table_info(customers)")]
        self.assertEqual(columns, ["id", "name", "email"])

    def test_versioned_fixture_restores_a_partially_expanded_database(self):
        snapshot = fixture("rollback_snapshot.json")
        expand_schema(self.connection)
        backfill_batch(self.connection, 2)

        rollback_to_legacy(self.connection, snapshot)
        self.assertEqual(migration_status(self.connection)["phase"], "legacy")
        self.assertEqual(capture_rollback_fixture(self.connection), snapshot)

    def test_batch_size_must_be_a_positive_integer(self):
        expand_schema(self.connection)
        for invalid in (0, -1, True, 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    backfill_batch(self.connection, invalid)


if __name__ == "__main__":
    unittest.main()
