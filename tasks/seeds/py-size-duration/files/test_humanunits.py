"""Acceptance checks for humanunits.py. Run: python3 test_humanunits.py"""
from humanunits import (parse_duration, format_duration,
                        parse_size, format_size)


# -------------------------------------------------------------- durations

def test_parse_duration_values():
    assert parse_duration("90s") == 90
    assert parse_duration("1h30m") == 5400
    assert parse_duration("1.5h") == 5400
    assert parse_duration("2d") == 172800
    assert parse_duration("1w1d") == 691200
    assert parse_duration("250ms") == 0.25
    assert parse_duration("1m30s250ms") == 90.25
    assert parse_duration("-90s") == -90
    assert parse_duration("-1h30m") == -5400
    assert parse_duration("1h 30m") == 5400
    assert parse_duration("  1h  ") == 3600
    assert parse_duration("0s") == 0


def test_parse_duration_rejects_garbage():
    bad = ["", "   ", "90", "1x", "h", "1h30", "1H", "30M",
           "30m1h", "1h1h", "1s500ms250ms", "--90s", "1.5.2h",
           "1h-30m", 90, None]
    for s in bad:
        try:
            parse_duration(s)
            assert False, "parse_duration(%r) did not raise" % (s,)
        except ValueError:
            pass


def test_format_duration_canonical():
    assert format_duration(0) == "0s"
    assert format_duration(5400) == "1h30m"
    assert format_duration(86400) == "1d"
    assert format_duration(691200) == "1w1d"
    assert format_duration(90.25) == "1m30s250ms"
    assert format_duration(0.5) == "500ms"
    assert format_duration(-3661) == "-1h1m1s"
    assert format_duration(3600) == "1h"      # no zero-components anywhere


def test_duration_round_trip():
    for secs in [0, 1, 59, 60, 90, 3600, 5400, 86400, 90.25, 0.001,
                 604800, 691200, 123456.75, -45, -5400]:
        rendered = format_duration(secs)
        back = parse_duration(rendered)
        assert back == secs, "%r -> %r -> %r" % (secs, rendered, back)


# ------------------------------------------------------------- byte sizes

def test_parse_size_values():
    assert parse_size("512") == 512
    assert parse_size("0") == 0
    assert parse_size("1KB") == 1000
    assert parse_size("1KiB") == 1024
    assert parse_size("1.5GiB") == 1610612736
    assert parse_size("2 MB") == 2000000
    assert parse_size("1gib") == 1073741824
    assert parse_size("10MiB") == 10485760
    assert parse_size("3TB") == 3000000000000
    assert parse_size("0.1KB") == 100
    assert parse_size(" 42 b ") == 42


def test_parse_size_rounds_half_up():
    assert parse_size("1.5B") == 2
    assert parse_size("2.5B") == 3          # half-up, not banker's rounding
    assert parse_size("1.4B") == 1
    assert parse_size("0.001KB") == 1


def test_parse_size_rejects_garbage():
    bad = ["", "KB", "1PB", "1 K B", "kb1", "1..5KB", "-1KB", "-5",
           "1,5GB", "1e3B", 1024, None]
    for s in bad:
        try:
            parse_size(s)
            assert False, "parse_size(%r) did not raise" % (s,)
        except ValueError:
            pass


def test_format_size_binary_and_decimal():
    assert format_size(0) == "0B"
    assert format_size(999) == "999B"
    assert format_size(1023) == "1023B"
    assert format_size(1024) == "1KiB"
    assert format_size(1536) == "1.5KiB"
    assert format_size(1073741824) == "1GiB"
    assert format_size(1500, binary=False) == "1.5KB"
    assert format_size(1000, binary=False) == "1KB"
    assert format_size(999, binary=False) == "999B"
    assert format_size(2500000, binary=False) == "2.5MB"
    assert format_size(1500) == "1.46KiB"   # <=2 decimals, trailing zeros gone
    try:
        format_size(-1)
        assert False, "formatted a negative size"
    except ValueError:
        pass


def test_size_round_trip():
    for n in [0, 1, 999, 1024, 1536, 10485760, 1610612736]:
        rendered = format_size(n)
        assert parse_size(rendered) == n, "binary %r via %r" % (n, rendered)
    for n in [0, 1, 999, 1000, 1500, 2500000, 3000000000000]:
        rendered = format_size(n, binary=False)
        assert parse_size(rendered) == n, "decimal %r via %r" % (n, rendered)


CHECKS = [
    test_parse_duration_values,
    test_parse_duration_rejects_garbage,
    test_format_duration_canonical,
    test_duration_round_trip,
    test_parse_size_values,
    test_parse_size_rounds_half_up,
    test_parse_size_rejects_garbage,
    test_format_size_binary_and_decimal,
    test_size_round_trip,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
