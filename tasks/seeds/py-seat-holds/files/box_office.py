"""Seat-hold service for the box-office API (asyncio end to end).

The HTTP layer awaits hold_seats() once per checkout. Seat inventory
lives behind an async store — Redis in production, InMemorySeats in
tests — because every lookup crosses the network. A hold that would
oversell a show must be rejected with SoldOut and leave the inventory
untouched; overselling a venue is the one thing this service exists to
prevent.
"""
import itertools


class SoldOut(RuntimeError):
    """The show cannot cover the requested number of seats."""


class InMemorySeats:
    """Async seat store mirroring the production Redis client's interface."""

    def __init__(self, counts):
        self._counts = dict(counts)

    async def available(self, show):
        return self._counts.get(show, 0)

    async def has_capacity(self, show, qty):
        return self._counts.get(show, 0) >= qty

    async def take(self, show, qty):
        self._counts[show] = self._counts.get(show, 0) - qty


class BoxOffice:
    def __init__(self, seats):
        self._seats = seats
        self._hold_ids = itertools.count(1)
        self.holds = []

    async def hold_seats(self, show, qty):
        """Place a hold on *qty* seats for *show*; returns the hold record."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        if not self._seats.has_capacity(show, qty):
            raise SoldOut(f"{show}: cannot hold {qty} seats")
        await self._seats.take(show, qty)
        hold = {"id": f"H{next(self._hold_ids)}", "show": show, "qty": qty}
        self.holds.append(hold)
        return hold

    async def held_total(self, show):
        """Seats currently held for a show across all holds."""
        return sum(h["qty"] for h in self.holds if h["show"] == show)
