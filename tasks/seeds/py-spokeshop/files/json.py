"""JSON helpers for the shop's nightly order export."""
import json


def order_line(order):
    """One order as a compact, key-sorted JSON line."""
    return json.dumps(order, sort_keys=True, separators=(",", ":"))


def read_lines(text):
    """Parse a nightly export back into order dicts."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]
