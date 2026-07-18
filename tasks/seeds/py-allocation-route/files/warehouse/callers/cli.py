"""Counter CLI adapter."""

from warehouse.domain.allocation import AllocationService
from warehouse.ports.inventory import InventoryPort, LedgerPort


def allocate_line(
    service: AllocationService,
    inventory: InventoryPort,
    ledger: LedgerPort,
    sku: str,
    quantity: int,
    ship_on: str,
) -> str:
    plan = service.allocate(inventory, ledger, sku, quantity, ship_on)
    return ", ".join(
        f"{entry.location} x{entry.quantity} ({entry.source})" for entry in plan
    )
