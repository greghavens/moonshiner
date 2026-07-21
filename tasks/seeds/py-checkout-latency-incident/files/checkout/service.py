"""Checkout orchestration. Dependencies retain ownership of their limits."""

from .budget import RequestBudget
from .errors import (
    CheckoutRejected,
    CheckoutTimedOut,
    InventoryUnavailable,
    TransportTimeout,
)


class CheckoutService:
    def __init__(self, inventory, payment, logger, clock):
        self._inventory = inventory
        self._payment = payment
        self._logger = logger
        self._clock = clock

    def submit(self, checkout_id, lines, *, budget_ms=2000):
        budget = RequestBudget(self._clock, budget_ms)
        self._logger.emit(
            "checkout.started",
            checkout_id=checkout_id,
            budget_ms=budget_ms,
        )
        try:
            reservation_id = self._inventory.reserve(checkout_id, lines, budget)
        except InventoryUnavailable as error:
            self._logger.emit(
                "checkout.rejected",
                checkout_id=checkout_id,
                reason="inventory_unavailable",
                error_type=type(error).__name__,
                status=error.status,
                request_id=error.request_id,
                detail=error.detail,
                error=str(error),
            )
            raise CheckoutRejected(checkout_id, "inventory unavailable") from error
        except TransportTimeout as error:
            self._logger.emit(
                "checkout.timed_out",
                checkout_id=checkout_id,
                dependency="inventory",
                operation=error.operation,
                error_type=type(error).__name__,
                error=str(error),
            )
            raise CheckoutTimedOut(checkout_id) from error

        payment_id = self._payment.charge(
            checkout_id,
            reservation_id,
            lines,
            budget=budget,
        )
        self._logger.emit(
            "checkout.completed",
            checkout_id=checkout_id,
            reservation_id=reservation_id,
            payment_id=payment_id,
        )
        return {
            "checkout_id": checkout_id,
            "reservation_id": reservation_id,
            "payment_id": payment_id,
        }

