"""Allocation planning and atomic ledger commit."""

from warehouse.domain.models import Reservation
from warehouse.ports.inventory import InventoryPort, LedgerPort


class AllocationService:
    """Plans a reservation and commits it only when the request is fillable."""

    def allocate(
        self,
        inventory: InventoryPort,
        ledger: LedgerPort,
        sku: str,
        quantity: int,
        ship_on: str,
    ) -> list[Reservation]:
        if quantity <= 0:
            raise ValueError("quantity must be positive")

        lots = inventory.lots_for(sku)
        remaining = quantity
        planned: list[Reservation] = []
        for lot in lots:
            if lot.sku != sku or lot.available <= 0 or lot.expires_on < ship_on:
                continue
            take = min(remaining, lot.available)
            planned.append(
                Reservation(sku, lot.location, take, lot.source, lot.expires_on)
            )
            remaining -= take
            if remaining == 0:
                break

        if remaining:
            raise LookupError(f"short {remaining} of {sku}")
        ledger.commit(planned)
        return planned
