"""Structural ports consumed by the allocation service."""

from typing import Protocol, Sequence

from warehouse.domain.models import Reservation, StockLot


class InventoryPort(Protocol):
    def lots_for(self, sku: str) -> Sequence[StockLot]: ...


class LedgerPort(Protocol):
    def commit(self, reservations: Sequence[Reservation]) -> None: ...
