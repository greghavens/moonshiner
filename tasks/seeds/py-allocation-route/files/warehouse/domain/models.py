"""Stable values shared by allocation ports and callers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class StockLot:
    sku: str
    location: str
    expires_on: str
    available: int
    source: str


@dataclass(frozen=True)
class Reservation:
    sku: str
    location: str
    quantity: int
    source: str
    expires_on: str
