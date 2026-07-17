"""Acceptance tests for the meter-usage ETL.

Timestamps are constructed explicitly in UTC; nothing here reads the
clock, the filesystem or the network. Run: python3 test_meter_etl.py
"""
import json
from datetime import datetime, timezone

import readings
import rollup
import report


def at(stamp):
    """Epoch seconds for a UTC timestamp like 2026-06-01T09:15:00."""
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    return int(moment.timestamp())


def jsonl(*records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


def test_gateway_csv_parses_to_watt_hours():
    text = (
        "meter,recorded_at,wh\n"
        "m1,2026-06-01T09:15:00,600\n"
        "m2,2026-06-01T11:00:00,505\n"
    )
    got = readings.combine_feeds(readings.parse_gateway_csv(text), [])
    assert got == [
        {"meter": "m1", "ts": at("2026-06-01T09:15:00"), "wh": 600},
        {"meter": "m2", "ts": at("2026-06-01T11:00:00"), "wh": 505},
    ], got


def test_archive_backfill_lands_in_watt_hours():
    text = jsonl(
        {"meter": "m2", "ts": at("2026-06-01T03:30:00"), "value": 0.5, "unit": "kwh"},
        {"meter": "m5", "ts": at("2026-06-01T04:30:00"), "value": 900, "unit": "wh"},
        {"meter": "m6", "ts": at("2026-06-01T05:30:00"), "value": 750},
    )
    got = readings.combine_feeds([], readings.parse_archive_jsonl(text))
    by_meter = {r["meter"]: r["wh"] for r in got}
    assert by_meter == {"m2": 500, "m5": 900, "m6": 750}, by_meter


def test_live_reading_wins_over_backfill_duplicate():
    ts = at("2026-06-01T09:45:00")
    gateway = readings.parse_gateway_csv(
        "meter,recorded_at,wh\nm1,2026-06-01T09:45:00,400\n"
    )
    archive = readings.parse_archive_jsonl(
        jsonl({"meter": "m1", "ts": ts, "value": 9.9, "unit": "kwh"})
    )
    got = readings.combine_feeds(gateway, archive)
    assert got == [{"meter": "m1", "ts": ts, "wh": 400}], got


def test_hourly_buckets_honor_the_hour_boundary():
    rows = [
        {"meter": "m1", "ts": at("2026-06-01T09:15:00"), "wh": 600},
        {"meter": "m1", "ts": at("2026-06-01T09:45:00"), "wh": 400},
        {"meter": "m1", "ts": at("2026-06-01T10:00:00"), "wh": 250},
        {"meter": "m1", "ts": at("2026-06-01T10:20:00"), "wh": 150},
    ]
    got = rollup.hourly_series(rows, "m1")
    assert got == [
        {"start": at("2026-06-01T09:00:00"), "wh": 1000},
        {"start": at("2026-06-01T10:00:00"), "wh": 400},
    ], got


def test_single_on_the_hour_reading_gets_its_own_bucket():
    rows = [
        {"meter": "m3", "ts": at("2026-06-01T09:15:00"), "wh": 320},
        {"meter": "m3", "ts": at("2026-06-01T10:00:00"), "wh": 210},
    ]
    got = rollup.hourly_series(rows, "m3")
    assert got == [
        {"start": at("2026-06-01T09:00:00"), "wh": 320},
        {"start": at("2026-06-01T10:00:00"), "wh": 210},
    ], got


def test_gap_hours_produce_no_buckets():
    rows = [
        {"meter": "m4", "ts": at("2026-06-01T09:10:00"), "wh": 100},
        {"meter": "m4", "ts": at("2026-06-01T13:25:00"), "wh": 900},
    ]
    got = rollup.hourly_series(rows, "m4")
    assert got == [
        {"start": at("2026-06-01T09:00:00"), "wh": 100},
        {"start": at("2026-06-01T13:00:00"), "wh": 900},
    ], got


def test_kwh_totals_round_half_up():
    # values finance checked by hand: exact half-hundredths go UP
    assert report.format_kwh(2345) == "2.35", report.format_kwh(2345)
    assert report.format_kwh(45) == "0.05", report.format_kwh(45)
    assert report.format_kwh(1465) == "1.47", report.format_kwh(1465)
    assert report.format_kwh(1005) == "1.01", report.format_kwh(1005)


def test_kwh_formatting_everyday_values():
    assert report.format_kwh(0) == "0.00"
    assert report.format_kwh(12) == "0.01"
    assert report.format_kwh(999) == "1.00"
    assert report.format_kwh(2340) == "2.34"
    assert report.format_kwh(2355) == "2.36"
    assert report.format_kwh(2470) == "2.47"


def test_midnight_reading_bills_to_the_new_day():
    rows = [
        {"meter": "m1", "ts": at("2026-06-01T23:40:00"), "wh": 120},
        {"meter": "m1", "ts": at("2026-06-02T00:00:00"), "wh": 300},
    ]
    got = report.daily_totals(rows)
    assert got == {
        ("2026-06-01", "m1"): 120,
        ("2026-06-02", "m1"): 300,
    }, got


def test_daily_report_end_to_end():
    gateway = (
        "meter,recorded_at,wh\n"
        "m1,2026-06-01T09:15:00,600\n"
        "m1,2026-06-01T09:45:00,400\n"
        "m1,2026-06-01T10:00:00,250\n"
        "m1,2026-06-01T10:20:00,95\n"
        "m1,2026-06-01T23:40:00,120\n"
        "m1,2026-06-02T00:00:00,300\n"
        "m1,2026-06-02T08:30:00,145\n"
        "m2,2026-06-01T11:00:00,505\n"
    )
    archive = jsonl(
        {"meter": "m2", "ts": at("2026-06-01T03:30:00"), "value": 0.5, "unit": "kwh"},
        {"meter": "m1", "ts": at("2026-06-01T09:45:00"), "value": 9.9, "unit": "kwh"},
    )
    got = report.build_report(gateway, archive)
    assert got == [
        "2026-06-01 m1 1.47 kWh",
        "2026-06-01 m2 1.01 kWh",
        "2026-06-01 total 2.47 kWh",
        "2026-06-02 m1 0.45 kWh",
        "2026-06-02 total 0.45 kWh",
    ], "\n".join(got)


def main():
    tests = [
        test_gateway_csv_parses_to_watt_hours,
        test_archive_backfill_lands_in_watt_hours,
        test_live_reading_wins_over_backfill_duplicate,
        test_hourly_buckets_honor_the_hour_boundary,
        test_single_on_the_hour_reading_gets_its_own_bucket,
        test_gap_hours_produce_no_buckets,
        test_kwh_totals_round_half_up,
        test_kwh_formatting_everyday_values,
        test_midnight_reading_bills_to_the_new_day,
        test_daily_report_end_to_end,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
