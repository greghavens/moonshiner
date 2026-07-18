"""Request parsing and response serialization for allocation."""

from warehouse.domain.allocation import AllocationService
from warehouse.ports.inventory import InventoryPort, LedgerPort


def post_allocation(
    payload: dict,
    service: AllocationService,
    inventory: InventoryPort,
    ledger: LedgerPort,
) -> dict:
    reservations = service.allocate(
        inventory,
        ledger,
        payload["sku"],
        payload["quantity"],
        payload["ship_on"],
    )
    return {
        "sku": payload["sku"],
        "allocations": [
            {
                "location": entry.location,
                "quantity": entry.quantity,
                "source": entry.source,
            }
            for entry in reservations
        ],
    }
