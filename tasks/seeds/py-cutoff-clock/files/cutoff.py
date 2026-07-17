"""Same-day shipment cutoff decisions for a warehouse pickup schedule."""

from datetime import datetime, time, timedelta


class CutoffCalculator:
    """Decides which carrier pickup an order makes.

    Orders placed at or before the cutoff on the warehouse wall clock make
    that day's pickup; later orders roll to the next day.
    """

    def __init__(self, warehouse_tz, cutoff=time(16, 30)):
        self.tz = warehouse_tz
        self.cutoff = cutoff

    def normalize(self, placed_at):
        """Express an order timestamp on the warehouse wall clock."""
        if placed_at.tzinfo is not None:
            return placed_at.astimezone(self.tz)
        return placed_at

    def deadline_for(self, placed_at):
        """The pickup deadline governing this order."""
        return datetime.combine(placed_at.date(), self.cutoff)

    def makes_cutoff(self, placed_at):
        """True when the order makes the same-day pickup."""
        return self.normalize(placed_at) <= self.deadline_for(placed_at)

    def ship_date(self, placed_at):
        """The calendar day the order leaves the warehouse."""
        day = self.normalize(placed_at).date()
        if self.makes_cutoff(placed_at):
            return day
        return day + timedelta(days=1)

    def status(self, placed_at):
        """Summary used by the order-status endpoint."""
        return {
            "deadline": self.deadline_for(placed_at).isoformat(),
            "ships_on": self.ship_date(placed_at).isoformat(),
            "same_day": self.makes_cutoff(placed_at),
        }
