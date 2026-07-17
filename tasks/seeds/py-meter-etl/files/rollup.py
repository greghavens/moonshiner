"""Hourly rollup of canonical meter readings.

Readings are grouped per meter into hour-aligned windows
``[start, start + 3600)`` in epoch seconds. Hours with no readings
produce no bucket at all (billing treats a missing hour as zero usage),
so a series only contains hours that actually saw usage.
"""

HOUR = 3600


def window_start(ts):
    """Start of the hour-aligned window containing ``ts``."""
    return ts - ts % HOUR


def hourly_series(readings, meter):
    """Ordered ``[{"start": epoch, "wh": int}]`` buckets for one meter.

    Readings may arrive in any order; they are processed by timestamp.
    """
    rows = sorted(
        (r for r in readings if r["meter"] == meter),
        key=lambda r: r["ts"],
    )
    buckets = []
    start = None
    total = 0
    for reading in rows:
        if start is None:
            start = window_start(reading["ts"])
        elif reading["ts"] > start + HOUR:
            buckets.append({"start": start, "wh": total})
            start = window_start(reading["ts"])
            total = 0
        total += reading["wh"]
    if start is not None:
        buckets.append({"start": start, "wh": total})
    return buckets


def meters(readings):
    """Sorted list of every meter id present in the readings."""
    return sorted({r["meter"] for r in readings})


def series_total(buckets):
    """Total watt-hours across a list of hourly buckets."""
    return sum(b["wh"] for b in buckets)
