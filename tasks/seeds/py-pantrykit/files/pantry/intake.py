"""Donation intake for the front desk."""
from dataclasses import dataclass

from pantry.reports import weekly_summary
from pantry.storage import BinIndex
from pantry.util import normalize_name, parse_qty


@dataclass
class IntakeRecord:
    donor: str
    item: str
    qty: float
    unit: str
    day: int  # day number within the season, 1-based
    category: str


def record_donation(donor, item, qty_text, day, category):
    """Build a normalized intake record from what the volunteer typed."""
    qty, unit = parse_qty(qty_text)
    return IntakeRecord(
        donor=normalize_name(donor),
        item=normalize_name(item),
        qty=qty,
        unit=unit,
        day=day,
        category=category,
    )


def shelve_records(records, bins):
    """Place each record into the shelf bins; returns the BinIndex used."""
    index = BinIndex(bins)
    for rec in records:
        index.place(rec.category, rec.qty)
    return index


def intake_digest(records):
    """Convenience wrapper the desk sheet prints at closing time."""
    return weekly_summary(records)
