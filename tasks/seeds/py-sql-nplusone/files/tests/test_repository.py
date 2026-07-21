from __future__ import annotations

import sqlite3
import unittest

from article_feed import ArticleRepository


SCHEMA = """
CREATE TABLE authors (
    id INTEGER NOT NULL,
    tenant_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    is_active INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    tenant_id INTEGER NOT NULL,
    author_id INTEGER,
    title TEXT NOT NULL,
    score INTEGER NOT NULL,
    published_at TEXT NOT NULL,
    is_published INTEGER NOT NULL
);
"""


class ArticleRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(SCHEMA)
        self.connection.executemany(
            "INSERT INTO authors VALUES (?, ?, ?, ?)",
            [
                (1, 7, "Ada", 1),
                (2, 7, "Grace", 1),
                (3, 7, "Retired", 0),
                (1, 8, "Other tenant", 1),
            ],
        )
        self.connection.executemany(
            "INSERT INTO articles VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (10, 7, 1, "Newest by Ada", 90, "2026-04-03T09:00:00Z", 1),
                (11, 7, 2, "Same time by Grace", 70, "2026-04-02T09:00:00Z", 1),
                (12, 7, 99, "Missing author", 80, "2026-04-02T09:00:00Z", 1),
                (13, 7, 3, "Inactive author", 60, "2026-04-01T09:00:00Z", 1),
                (14, 7, 1, "Draft", 100, "2026-04-04T09:00:00Z", 0),
                (15, 7, 2, "Below threshold", 10, "2026-04-05T09:00:00Z", 1),
                (20, 8, 1, "Different tenant", 100, "2026-04-06T09:00:00Z", 1),
            ],
        )
        self.connection.commit()
        self.repository = ArticleRepository(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_filters_orders_and_keeps_missing_associations(self) -> None:
        articles = self.repository.list_feed(tenant_id=7, minimum_score=50)

        self.assertEqual([10, 11, 12, 13], [article.id for article in articles])
        self.assertEqual(
            ["Ada", "Grace", None, None],
            [article.author.display_name if article.author else None for article in articles],
        )

    def test_uses_two_selects_independent_of_result_count(self) -> None:
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)

        articles = self.repository.list_feed(tenant_id=7, minimum_score=50)

        selects = [sql for sql in statements if sql.lstrip().upper().startswith("SELECT")]
        self.assertEqual(4, len(articles))
        self.assertEqual(2, len(selects), statements)
        self.assertIn("FROM articles", selects[0])
        self.assertIn("FROM authors", selects[1])

    def test_all_queries_stay_inside_repository_owned_transaction(self) -> None:
        statements: list[str] = []
        self.connection.set_trace_callback(statements.append)

        self.repository.list_feed(tenant_id=7, minimum_score=50)

        significant = [statement.strip().upper() for statement in statements]
        self.assertEqual("BEGIN", significant[0])
        self.assertEqual("COMMIT", significant[-1])
        self.assertTrue(all("SELECT" in sql for sql in significant[1:-1]))

    def test_does_not_finish_callers_transaction(self) -> None:
        self.connection.execute("BEGIN")

        self.repository.list_feed(tenant_id=7, minimum_score=50)

        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()


if __name__ == "__main__":
    unittest.main()
