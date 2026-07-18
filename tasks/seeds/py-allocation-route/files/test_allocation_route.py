from dataclasses import asdict

from warehouse.api.routes import post_allocation
from warehouse.callers.batch import allocate_batch
from warehouse.callers.cli import allocate_line
from warehouse.domain.allocation import AllocationService
from warehouse.domain.models import Reservation, StockLot
from warehouse.infrastructure.memory import MemoryInventory, MemoryLedger


SHIP_ON = "2026-08-01"


def red_lots():
    return [
        StockLot("SKU-RED", "EXPIRED", "2026-07-31", 9, "receipt-old"),
        StockLot("SKU-RED", "EMPTY", "2026-08-02", 0, "receipt-empty"),
        StockLot("SKU-RED", "LATE", "2026-11-01", 8, "receipt-late"),
        StockLot("SKU-RED", "B2", "2026-08-12", 2, "receipt-b2"),
        StockLot("SKU-RED", "A1", "2026-08-12", 3, "receipt-a1"),
        StockLot("SKU-BLUE", "FOREIGN", "2026-08-05", 20, "receipt-blue"),
    ]


class ScrambledInventory:
    """A second port implementation, deliberately unrelated to MemoryInventory."""

    def __init__(self, lots):
        self._rows = tuple(lots)
        self.calls = []

    def lots_for(self, sku):
        self.calls.append(sku)
        # This port implementation deliberately returns a noisy warehouse-wide
        # view. The domain boundary still owns both SKU eligibility and order.
        return self._rows[2:] + self._rows[:2]


def check_api_route_and_complete_ledger():
    ledger = MemoryLedger()
    response = post_allocation(
        {"sku": "SKU-RED", "quantity": 4, "ship_on": SHIP_ON},
        AllocationService(),
        MemoryInventory(red_lots()),
        ledger,
    )
    assert response == {
        "sku": "SKU-RED",
        "allocations": [
            {"location": "A1", "quantity": 3, "source": "receipt-a1"},
            {"location": "B2", "quantity": 1, "source": "receipt-b2"},
        ],
    }, f"API allocation order/shape was {response!r}"
    assert [asdict(entry) for entry in ledger.entries] == [
        {
            "sku": "SKU-RED",
            "location": "A1",
            "quantity": 3,
            "source": "receipt-a1",
            "expires_on": "2026-08-12",
        },
        {
            "sku": "SKU-RED",
            "location": "B2",
            "quantity": 1,
            "source": "receipt-b2",
            "expires_on": "2026-08-12",
        },
    ], "ledger entries lost or changed stable reservation fields"
    assert ledger.commits == 1


def check_rule_is_owned_above_concrete_inventory():
    inventory = ScrambledInventory(red_lots())
    ledger = MemoryLedger()
    plan = AllocationService().allocate(
        inventory, ledger, "SKU-RED", 6, SHIP_ON
    )
    assert [entry.location for entry in plan] == ["A1", "B2", "LATE"]
    assert [entry.quantity for entry in plan] == [3, 2, 1]
    assert inventory.calls == ["SKU-RED"]
    assert ledger.entries == plan


def check_expiry_order_is_independent_of_location_order():
    lots = [
        StockLot("SKU-AMBER", "A-LATER", "2026-10-10", 4, "receipt-later"),
        StockLot("SKU-AMBER", "Z-EXPIRING", "2026-08-03", 2, "receipt-expiring"),
    ]
    ledger = MemoryLedger()
    plan = AllocationService().allocate(
        ScrambledInventory(lots), ledger, "SKU-AMBER", 3, SHIP_ON
    )
    assert [entry.location for entry in plan] == ["Z-EXPIRING", "A-LATER"], (
        "expiry must win even when a later-expiring location sorts first"
    )
    assert [entry.quantity for entry in plan] == [2, 1]


def check_cli_and_batch_callers():
    service = AllocationService()
    cli_ledger = MemoryLedger()
    line = allocate_line(
        service,
        ScrambledInventory(red_lots()),
        cli_ledger,
        "SKU-RED",
        4,
        SHIP_ON,
    )
    assert line == "A1 x3 (receipt-a1), B2 x1 (receipt-b2)"

    lots = red_lots() + [
        StockLot("SKU-GREEN", "G9", "2026-10-01", 2, "receipt-g9"),
        StockLot("SKU-GREEN", "G1", "2026-09-01", 2, "receipt-g1"),
    ]
    batch_ledger = MemoryLedger()
    result = allocate_batch(
        service,
        ScrambledInventory(lots),
        batch_ledger,
        [
            {"sku": "SKU-RED", "quantity": 1, "ship_on": SHIP_ON},
            {"sku": "SKU-GREEN", "quantity": 3, "ship_on": SHIP_ON},
        ],
    )
    assert result == [["A1"], ["G1", "G9"]]
    assert [entry.sku for entry in batch_ledger.entries] == [
        "SKU-RED",
        "SKU-GREEN",
        "SKU-GREEN",
    ]


def check_shortage_is_atomic_and_validation_is_stable():
    ledger = MemoryLedger()
    try:
        AllocationService().allocate(
            ScrambledInventory(red_lots()), ledger, "SKU-RED", 99, SHIP_ON
        )
        raise AssertionError("short allocation should fail")
    except LookupError as exc:
        assert str(exc) == "short 86 of SKU-RED"
    assert ledger.entries == []
    assert ledger.commits == 0

    for bad in (0, -2):
        try:
            AllocationService().allocate(
                ScrambledInventory(red_lots()), ledger, "SKU-RED", bad, SHIP_ON
            )
            raise AssertionError("non-positive quantity should fail")
        except ValueError as exc:
            assert str(exc) == "quantity must be positive"
    assert ledger.commits == 0


def check_reservation_schema():
    entry = Reservation("SKU-X", "R1", 2, "receipt-x", "2026-12-01")
    assert list(asdict(entry)) == [
        "sku",
        "location",
        "quantity",
        "source",
        "expires_on",
    ]


def main():
    check_api_route_and_complete_ledger()
    check_rule_is_owned_above_concrete_inventory()
    check_expiry_order_is_independent_of_location_order()
    check_cli_and_batch_callers()
    check_shortage_is_atomic_and_validation_is_stable()
    check_reservation_schema()
    print("all allocation route checks passed")


if __name__ == "__main__":
    main()
