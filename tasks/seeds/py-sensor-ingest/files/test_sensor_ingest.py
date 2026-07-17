"""Behavior checks for the gateway line parser.

Run: python3 test_sensor_ingest.py
"""
from sensor_ingest import ingest, parse_line, sensor_averages


def test_canonical_conversions():
    assert parse_line("bay3-north temp 71.6 F") == {
        "sensor": "bay3-north", "kind": "temp", "value": 22.0}
    assert parse_line("bay3-north temp 295.65 K") == {
        "sensor": "bay3-north", "kind": "temp", "value": 22.5}
    assert parse_line("bay2-east temp 18.4 C")["value"] == 18.4
    assert parse_line("bay1-roof pressure 101.3 kPa")["value"] == 1013.0
    assert parse_line("bay1-roof pressure 1.02 bar")["value"] == 1020.0
    assert parse_line("bay4-west humidity 54 pct")["value"] == 54.0


def test_millibar_sites_report_real_pressure():
    reading = parse_line("bay1-roof pressure 1013.2 mbar")
    assert reading is not None, "mbar is a supported pressure unit"
    assert reading["value"] == 1013.2, (
        f"1013.2 mbar is 1013.2 hPa, got {reading['value']}"
    )


def test_freezing_point_is_a_real_reading():
    readings, rejected = ingest(["cold-frame temp 32 F"])
    assert rejected == [], rejected
    assert readings == [{"sensor": "cold-frame", "kind": "temp", "value": 0.0}], readings


def test_garbage_is_rejected_not_graphed():
    batch = [
        "# nightly batch from hub bay3",
        "",
        "bay3-north temp 71.6 F",
        "bay3-north temp",
        "bay3-south temp 7l.6 F",
        "\x02\x02corrupt frame 88",
        "bay3-east flux 1.4 W",
        "bay3-west temp 70.0 X",
        "bay3-north humidity 51 pct",
    ]
    readings, rejected = ingest(batch)
    assert [r["value"] for r in readings] == [22.0, 51.0], readings
    assert rejected == [
        "bay3-north temp",
        "bay3-south temp 7l.6 F",
        "\x02\x02corrupt frame 88",
        "bay3-east flux 1.4 W",
        "bay3-west temp 70.0 X",
    ], rejected
    for reading in readings:
        assert reading["kind"] in {"temp", "humidity", "pressure"}, reading


def test_dashboard_averages():
    batch = [
        "bay1-roof pressure 1013.2 mbar",
        "bay1-roof pressure 1009.8 mbar",
        "bay2-east pressure 101.02 kPa",
        "hub reboot marker",
        "bay1-roof temp 20.0 C",
    ]
    readings, rejected = ingest(batch)
    assert len(rejected) == 1, rejected
    averages = sensor_averages(readings, "pressure")
    assert averages == {"bay1-roof": 1011.5, "bay2-east": 1010.2}, averages


def main():
    test_canonical_conversions()
    test_millibar_sites_report_real_pressure()
    test_freezing_point_is_a_real_reading()
    test_garbage_is_rejected_not_graphed()
    test_dashboard_averages()
    print("all checks passed")


if __name__ == "__main__":
    main()
