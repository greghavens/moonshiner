"""ORM-like data mapping for the article feed."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class Author:
    id: int
    display_name: str


@dataclass(frozen=True)
class Article:
    id: int
    title: str
    score: int
    published_at: str
    author: Author | None


class ArticleRepository:
    """Maps feed rows and their optional authors to domain objects."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @contextmanager
    def _read_transaction(self) -> Iterator[None]:
        """Open a read transaction unless the caller already owns one."""
        owns_transaction = not self._connection.in_transaction
        if owns_transaction:
            self._connection.execute("BEGIN")
        try:
            yield
        except BaseException:
            if owns_transaction:
                self._connection.rollback()
            raise
        else:
            if owns_transaction:
                self._connection.commit()

    def list_feed(
        self, tenant_id: int, minimum_score: int = 0
    ) -> list[Article]:
        """Return the tenant's published feed in stable presentation order."""
        with self._read_transaction():
            article_rows = self._connection.execute(
                """
                SELECT id, title, score, published_at, author_id
                FROM articles
                WHERE tenant_id = ?
                  AND is_published = 1
                  AND score >= ?
                ORDER BY published_at DESC, id ASC
                """,
                (tenant_id, minimum_score),
            ).fetchall()

            articles: list[Article] = []
            for row in article_rows:
                author_row = self._connection.execute(
                    """
                    SELECT id, display_name
                    FROM authors
                    WHERE tenant_id = ? AND id = ? AND is_active = 1
                    """,
                    (tenant_id, row[4]),
                ).fetchone()
                author = (
                    Author(id=author_row[0], display_name=author_row[1])
                    if author_row is not None
                    else None
                )
                articles.append(
                    Article(
                        id=row[0],
                        title=row[1],
                        score=row[2],
                        published_at=row[3],
                        author=author,
                    )
                )
            return articles
