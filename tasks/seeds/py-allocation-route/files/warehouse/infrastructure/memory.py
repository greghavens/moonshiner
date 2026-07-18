"""Simple inventory and ledger adapters."""

from warehouse.domain.models import Reservation, StockLot


class MemoryInventory:
    def __init__(self, lots: list[StockLot]):
        self._lots = list(lots)

    def lots_for(self, sku: str) -> list[StockLot]:
        return [lot for lot in self._lots if lot.sku == sku]


class MemoryLedger:
    def __init__(self):
        self.entries: list[Reservation] = []
        self.commits = 0

    def commit(self, reservations: list[Reservation]) -> None:
        self.commits += 1
        self.entries.extend(reservations)
