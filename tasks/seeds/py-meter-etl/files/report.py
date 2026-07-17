"""Daily usage report over the hourly rollup.

The report has one line per meter per day, sorted by (date, meter),
followed by a ``total`` line per day:

    2026-06-01 m1 1.47 kWh
    2026-06-01 m2 1.01 kWh
    2026-06-01 total 2.47 kWh

Figures are kilowatt-hours with two decimals. Finance rounds half-up:
0.005 kWh always shows as 0.01. Day totals are computed from the raw
watt-hours and rounded once at the end, never by summing rounded lines.
"""
from datetime import datetime, timezone

import readings as feeds
import rollup

DAY = 86400


def format_kwh(wh):
    """Render an integer count of watt-hours as a two-decimal kWh string."""
    centi = round(wh / 10)
    return "%d.%02d" % divmod(centi, 100)


def date_str(ts):
    """UTC calendar date of an epoch timestamp."""
    day_start = ts - ts % DAY
    return datetime.fromtimestamp(day_start, tz=timezone.utc).strftime("%Y-%m-%d")


def daily_totals(canonical):
    """``{(date, meter): wh}`` accumulated from the hourly buckets."""
    totals = {}
    for meter in rollup.meters(canonical):
        for bucket in rollup.hourly_series(canonical, meter):
            key = (date_str(bucket["start"]), meter)
            totals[key] = totals.get(key, 0) + bucket["wh"]
    return totals


def build_report(gateway_text, archive_text):
    """Run the whole pipeline and return the report lines."""
    canonical = feeds.combine_feeds(
        feeds.parse_gateway_csv(gateway_text),
        feeds.parse_archive_jsonl(archive_text),
    )
    totals = daily_totals(canonical)
    lines = []
    for day in sorted({date for date, _ in totals}):
        day_wh = 0
        for (date, meter), wh in sorted(totals.items()):
            if date == day:
                lines.append("%s %s %s kWh" % (date, meter, format_kwh(wh)))
                day_wh += wh
        lines.append("%s total %s kWh" % (day, format_kwh(day_wh)))
    return lines
