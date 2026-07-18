"""Nightly order allocator."""

from warehouse.domain.allocation import AllocationService
from warehouse.ports.inventory import InventoryPort, LedgerPort


def allocate_batch(
    service: AllocationService,
    inventory: InventoryPort,
    ledger: LedgerPort,
    orders: list[dict],
) -> list[list[str]]:
    completed = []
    for order in orders:
        plan = service.allocate(
            inventory,
            ledger,
            order["sku"],
            order["quantity"],
            order["ship_on"],
        )
        completed.append([entry.location for entry in plan])
    return completed
