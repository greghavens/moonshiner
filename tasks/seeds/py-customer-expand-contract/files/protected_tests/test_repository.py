import json
import unittest
from pathlib import Path

from customer_names import (
    CustomerRepository,
    backfill_batch,
    connect_memory,
    contract_schema,
    expand_schema,
    load_legacy_fixture,
)


ROOT = Path(__file__).resolve().parents[1]


class RepositoryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_memory()
        data = json.loads(
            (ROOT / "fixtures" / "legacy_customers.json").read_text(encoding="utf-8")
        )
        load_legacy_fixture(self.connection, data)
        self.repository = CustomerRepository(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_legacy_reads_and_structured_callers_are_supported(self):
        ada = self.repository.get_customer(1)
        self.assertEqual((ada.given_name, ada.family_name, ada.display_name), ("Ada", "Lovelace", "Ada Lovelace"))
        created = self.repository.create_customer(
            email="katherine@example.test",
            given_name="Katherine",
            family_name="Johnson",
        )
        stored = self.connection.execute(
            "SELECT name FROM customers WHERE id = ?", (created.id,)
        ).fetchone()
        self.assertEqual(stored["name"], "Katherine Johnson")

    def test_expanded_repository_dual_reads_and_dual_writes(self):
        expand_schema(self.connection)
        legacy_cursor = self.connection.execute(
            "INSERT INTO customers(name, email) VALUES (?, ?)",
            ("New Legacy Writer", "legacy-writer@example.test"),
        )
        self.connection.commit()
        fallback = self.repository.get_customer(legacy_cursor.lastrowid)
        self.assertEqual((fallback.given_name, fallback.family_name), ("New Legacy", "Writer"))

        created = self.repository.create_customer(
            email="structured@example.test",
            given_name="New Client",
            family_name="Writer Junior",
        )
        stored = self.connection.execute(
            "SELECT name, given_name, family_name FROM customers WHERE id = ?",
            (created.id,),
        ).fetchone()
        self.assertEqual(
            tuple(stored),
            ("New Client Writer Junior", "New Client", "Writer Junior"),
        )
        renamed = self.repository.rename_customer(created.id, display_name="Single")
        self.assertEqual((renamed.given_name, renamed.family_name), ("Single", ""))
        stored = self.connection.execute(
            "SELECT name, given_name, family_name FROM customers WHERE id = ?",
            (created.id,),
        ).fetchone()
        self.assertEqual(tuple(stored), ("Single", "Single", ""))

    def test_repository_survives_contract_for_both_call_styles(self):
        expand_schema(self.connection)
        backfill_batch(self.connection, 50)
        contract_schema(self.connection)

        structured = self.repository.create_customer(
            email="post-contract@example.test", given_name="Post", family_name="Contract"
        )
        legacy = self.repository.create_customer(
            email="display@example.test", display_name="Display Caller"
        )
        self.assertEqual(structured.display_name, "Post Contract")
        self.assertEqual((legacy.given_name, legacy.family_name), ("Display", "Caller"))
        renamed = self.repository.rename_customer(1, given_name="Ada", family_name="King")
        self.assertEqual(renamed.display_name, "Ada King")


if __name__ == "__main__":
    unittest.main()
