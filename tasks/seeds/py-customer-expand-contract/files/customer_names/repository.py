"""Repository compatibility layer used while customer names are migrated."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional, Tuple


def _normalize(value: str) -> str:
    return " ".join(value.split())


def split_legacy_name(display_name: str) -> Tuple[str, str]:
    """Split a legacy display name using the migration's stable rule."""
    normalized = _normalize(display_name)
    if not normalized:
        raise ValueError("a customer name cannot be empty")
    if " " not in normalized:
        return normalized, ""
    return tuple(normalized.rsplit(" ", 1))  # type: ignore[return-value]


def compose_name(given_name: str, family_name: str) -> str:
    given = _normalize(given_name)
    family = _normalize(family_name)
    if not given:
        raise ValueError("given_name cannot be empty")
    return " ".join(part for part in (given, family) if part)


@dataclass(frozen=True)
class Customer:
    id: int
    email: str
    given_name: str
    family_name: str
    display_name: str


class CustomerRepository:
    """Read and write customers against legacy, expanded, or contracted tables."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def _columns(self) -> set[str]:
        return {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(customers)")
        }

    @staticmethod
    def _resolve_names(
        *,
        display_name: Optional[str],
        given_name: Optional[str],
        family_name: Optional[str],
    ) -> Tuple[str, str, str]:
        if given_name is None:
            if display_name is None:
                raise ValueError("provide display_name or given_name")
            given, family = split_legacy_name(display_name)
            return given, family, compose_name(given, family)

        family = "" if family_name is None else family_name
        canonical = compose_name(given_name, family)
        given = _normalize(given_name)
        family = _normalize(family)
        if display_name is not None and _normalize(display_name) != canonical:
            raise ValueError("display_name and structured names disagree")
        return given, family, canonical

    def create_customer(
        self,
        *,
        email: str,
        display_name: Optional[str] = None,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
    ) -> Customer:
        given, family, legacy = self._resolve_names(
            display_name=display_name,
            given_name=given_name,
            family_name=family_name,
        )
        columns = self._columns()
        with self.connection:
            if "name" in columns and "given_name" in columns:
                cursor = self.connection.execute(
                    "INSERT INTO customers(name, given_name, family_name, email) "
                    "VALUES (?, ?, ?, ?)",
                    (legacy, given, family, email),
                )
            elif "name" in columns:
                cursor = self.connection.execute(
                    "INSERT INTO customers(name, email) VALUES (?, ?)",
                    (legacy, email),
                )
            else:
                cursor = self.connection.execute(
                    "INSERT INTO customers(given_name, family_name, email) "
                    "VALUES (?, ?, ?)",
                    (given, family, email),
                )
        return self.get_customer(int(cursor.lastrowid))

    def rename_customer(
        self,
        customer_id: int,
        *,
        display_name: Optional[str] = None,
        given_name: Optional[str] = None,
        family_name: Optional[str] = None,
    ) -> Customer:
        given, family, legacy = self._resolve_names(
            display_name=display_name,
            given_name=given_name,
            family_name=family_name,
        )
        columns = self._columns()
        with self.connection:
            if "name" in columns and "given_name" in columns:
                cursor = self.connection.execute(
                    "UPDATE customers SET name = ?, given_name = ?, family_name = ? "
                    "WHERE id = ?",
                    (legacy, given, family, customer_id),
                )
            elif "name" in columns:
                cursor = self.connection.execute(
                    "UPDATE customers SET name = ? WHERE id = ?",
                    (legacy, customer_id),
                )
            else:
                cursor = self.connection.execute(
                    "UPDATE customers SET given_name = ?, family_name = ? WHERE id = ?",
                    (given, family, customer_id),
                )
        if cursor.rowcount != 1:
            raise KeyError(customer_id)
        return self.get_customer(customer_id)

    def get_customer(self, customer_id: int) -> Customer:
        columns = self._columns()
        selected = ["id", "email"]
        if "name" in columns:
            selected.append("name")
        if "given_name" in columns:
            selected.extend(("given_name", "family_name"))
        row = self.connection.execute(
            f"SELECT {', '.join(selected)} FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        if row is None:
            raise KeyError(customer_id)

        if "given_name" in columns and row["given_name"] is not None and row["family_name"] is not None:
            given, family = row["given_name"], row["family_name"]
        else:
            given, family = split_legacy_name(row["name"])
        return Customer(
            id=row["id"],
            email=row["email"],
            given_name=given,
            family_name=family,
            display_name=compose_name(given, family),
        )
