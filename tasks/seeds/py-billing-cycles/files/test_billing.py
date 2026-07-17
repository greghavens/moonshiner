"""Behavior checks for renewal-date math. Run: python3 test_billing.py"""
from datetime import date

from billing import next_annual, next_monthly, schedule


def main():
    # Monthly renewals stick to the anchor day.
    got = next_monthly(date(2026, 3, 15))
    assert got == date(2026, 4, 15), f"Mar 15 renews Apr 15, got {got.isoformat()}"

    got = next_monthly(date(2026, 8, 1))
    assert got == date(2026, 9, 1), f"Aug 1 renews Sep 1, got {got.isoformat()}"

    # Shorter next month clamps to its last day.
    got = next_monthly(date(2026, 1, 31))
    assert got == date(2026, 2, 28), f"Jan 31 renews Feb 28, got {got.isoformat()}"

    got = next_monthly(date(2024, 1, 30))
    assert got == date(2024, 2, 29), f"Jan 30 renews Feb 29 in a leap year, got {got.isoformat()}"

    # December wraps the year.
    got = next_monthly(date(2026, 12, 10))
    assert got == date(2027, 1, 10), f"got {got.isoformat()}"

    # A year of monthly renewals from the 15th never drifts off the 15th.
    dates = schedule(date(2026, 1, 15), 12)
    assert all(d.day == 15 for d in dates), (
        f"monthly renewals drifted off the anchor day: "
        f"{[d.isoformat() for d in dates]}")
    assert dates[0] == date(2026, 2, 15)
    assert dates[-1] == date(2027, 1, 15), f"got {dates[-1].isoformat()}"

    # Annual renewals land on the same month/day.
    got = next_annual(date(2027, 6, 10))
    assert got == date(2028, 6, 10), (
        f"annual renewal across a leap year must hold the date, got {got.isoformat()}")

    got = next_annual(date(2024, 2, 29))
    assert got == date(2025, 2, 28), f"Feb 29 clamps in a common year, got {got.isoformat()}"

    dates = schedule(date(2026, 8, 1), 3, plan="annual")
    assert dates == [date(2027, 8, 1), date(2028, 8, 1), date(2029, 8, 1)], (
        f"got {[d.isoformat() for d in dates]}")

    # Degenerate case.
    assert schedule(date(2026, 5, 5), 0) == []

    print("all checks passed")


if __name__ == "__main__":
    main()
