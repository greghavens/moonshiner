"""Renewal-date math for subscription billing.

Contract (see BILL-207): a monthly subscription renews on the same
day-of-month as the date it renews from; when the next month is shorter,
it renews on that month's last day. An annual subscription renews on the
same month and day one year later (Feb 29 renews on Feb 28 in common
years). schedule() chains these steps to project the next N renewals.
"""
from datetime import timedelta

MONTHLY = "monthly"
ANNUAL = "annual"


def next_monthly(current):
    """The renewal date one month after *current*."""
    return current + timedelta(days=30)


def next_annual(current):
    """The renewal date one year after *current*."""
    return current + timedelta(days=365)


def next_renewal(current, plan):
    if plan == MONTHLY:
        return next_monthly(current)
    if plan == ANNUAL:
        return next_annual(current)
    raise ValueError(f"unknown plan {plan!r}")


def schedule(start, cycles, plan=MONTHLY):
    """Project the next *cycles* renewal dates for a subscription that
    starts (or last renewed) on *start*."""
    if cycles < 0:
        raise ValueError("cycles must be >= 0")
    dates = []
    current = start
    for _ in range(cycles):
        current = next_renewal(current, plan)
        dates.append(current)
    return dates
