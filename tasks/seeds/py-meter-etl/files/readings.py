"""Feed readers for the meter-usage ETL.

Two feeds land on the ingest box:

* the live gateway CSV (``meter,recorded_at,wh``) — current readings,
  integer watt-hours, timestamps in UTC;
* archive JSONL — one object per line with ``meter``, ``ts`` (epoch
  seconds) and a measured ``value`` whose ``unit`` is ``"wh"`` or
  ``"kwh"``; used to backfill hours the gateway missed.

Everything downstream works on canonical readings shaped like
``{"meter": str, "ts": int, "wh": int}`` — epoch seconds and an integer
count of watt-hours.
"""
import json
from datetime import datetime, timezone

CSV_HEADER = "meter,recorded_at,wh"
CSV_TIME_FMT = "%Y-%m-%dT%H:%M:%S"


def parse_gateway_csv(text):
    """Parse a live gateway export into raw reading dicts."""
    rows = [line for line in text.splitlines() if line.strip()]
    if not rows or rows[0].strip() != CSV_HEADER:
        raise ValueError("unrecognized gateway export header")
    out = []
    for line in rows[1:]:
        meter, stamp, wh = (field.strip() for field in line.split(","))
        moment = datetime.strptime(stamp, CSV_TIME_FMT).replace(tzinfo=timezone.utc)
        out.append({
            "meter": meter,
            "ts": int(moment.timestamp()),
            "value": int(wh),
            "unit": "wh",
        })
    return out


def parse_archive_jsonl(text):
    """Parse an archive backfill file into raw reading dicts.

    Archive values arrive in whatever unit the archiver stored, so
    kilowatt-hour records are converted up front.
    """
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        value = rec["value"]
        unit = rec.get("unit", "wh")
        if unit == "kwh":
            value = value * 1000
        out.append({
            "meter": rec["meter"],
            "ts": int(rec["ts"]),
            "value": value,
            "unit": unit,
        })
    return out


def to_watt_hours(reading):
    """Canonicalize one raw reading to integer watt-hours."""
    value = reading["value"]
    if reading.get("unit") == "kwh":
        value = value * 1000
    return {"meter": reading["meter"], "ts": reading["ts"], "wh": int(round(value))}


def combine_feeds(gateway, archive):
    """Merge live and backfill readings into one canonical list.

    The live gateway wins whenever both feeds cover the same
    ``(meter, ts)``. Result is sorted by ``(ts, meter)``.
    """
    merged = {}
    for raw in archive:
        reading = to_watt_hours(raw)
        merged[(reading["meter"], reading["ts"])] = reading
    for raw in gateway:
        reading = to_watt_hours(raw)
        merged[(reading["meter"], reading["ts"])] = reading
    return sorted(merged.values(), key=lambda r: (r["ts"], r["meter"]))
