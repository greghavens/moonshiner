"""Small shared helpers: quantity parsing and name cleanup."""

UNIT_ALIASES = {
    "kilogram": "kg",
    "kilograms": "kg",
    "kgs": "kg",
    "lb": "lb",
    "lbs": "lb",
    "pound": "lb",
    "pounds": "lb",
    "cans": "can",
    "boxes": "box",
    "bags": "bag",
}


def normalize_name(name):
    """Collapse whitespace and lowercase — donors write these by hand."""
    return " ".join(name.strip().lower().split())


def parse_qty(text):
    """Parse '12 cans' / '3.5 kg' into (quantity, canonical unit)."""
    parts = text.strip().split()
    if len(parts) != 2:
        raise ValueError(f"quantity must be '<number> <unit>': {text!r}")
    try:
        qty = float(parts[0])
    except ValueError:
        raise ValueError(f"bad quantity number: {parts[0]!r}") from None
    if qty <= 0:
        raise ValueError(f"quantity must be positive: {text!r}")
    unit = parts[1].lower()
    return qty, UNIT_ALIASES.get(unit, unit)


def format_qty(qty, unit):
    """'12 can' / '3.5 kg' — trailing .0 trimmed for whole numbers."""
    return f"{qty:g} {unit}"
