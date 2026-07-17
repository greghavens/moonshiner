"""Rollups for the coordinator's weekly email."""
from collections import defaultdict

from pantry.intake import IntakeRecord


def weekly_summary(records):
    """Totals keyed by (week, category, unit); weeks are 7 days, 1-based."""
    totals = defaultdict(float)
    for rec in records:
        if not isinstance(rec, IntakeRecord):
            raise TypeError(f"expected IntakeRecord, got {type(rec).__name__}")
        week = (rec.day - 1) // 7 + 1
        totals[(week, rec.category, rec.unit)] += rec.qty
    return dict(totals)


def top_donors(records, n=3):
    """Donors by total quantity donated (any unit), name as tie-break."""
    totals = defaultdict(float)
    for rec in records:
        if not isinstance(rec, IntakeRecord):
            raise TypeError(f"expected IntakeRecord, got {type(rec).__name__}")
        totals[rec.donor] += rec.qty
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]
