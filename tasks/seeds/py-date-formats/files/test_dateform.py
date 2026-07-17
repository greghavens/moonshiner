"""Acceptance tests for the multi-format date engine. Run: python3 test_dateform.py"""
from datetime import datetime, timedelta, timezone


def expect_value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__}({args!r}) should raise ValueError")


def main():
    from dateform import parse_date, format_date

    # -- ISO date --
    d = parse_date("2026-07-10")
    assert d == datetime(2026, 7, 10) and d.tzinfo is None

    # -- ISO datetime, T or space, optional seconds --
    assert parse_date("2026-07-10T14:30") == datetime(2026, 7, 10, 14, 30)
    assert parse_date("2026-07-10 14:30") == datetime(2026, 7, 10, 14, 30)
    d = parse_date("2026-07-10T14:30:45")
    assert d == datetime(2026, 7, 10, 14, 30, 45) and d.tzinfo is None

    # -- UTC offsets make the result timezone-aware --
    d = parse_date("2026-03-01T12:00:00Z")
    assert d.tzinfo is not None and d.utcoffset() == timedelta(0)
    d = parse_date("2026-03-01T12:00:00+05:30")
    assert d.utcoffset() == timedelta(hours=5, minutes=30)
    d = parse_date("2026-03-01T12:00:00-08:00")
    assert d.utcoffset() == timedelta(hours=-8)
    # the same instant in two offsets compares equal
    assert parse_date("2026-03-01T12:00:00Z") == parse_date("2026-03-01T07:00:00-05:00")

    # -- US style: slash means month first, always --
    assert parse_date("7/4/2026") == datetime(2026, 7, 4)
    assert parse_date("07/04/2026") == datetime(2026, 7, 4)
    assert parse_date("3/14/2026") == datetime(2026, 3, 14)

    # -- EU style: dot means day first, always --
    assert parse_date("4.7.2026") == datetime(2026, 7, 4)
    assert parse_date("31.12.2026") == datetime(2026, 12, 31)
    assert parse_date("1.2.45") == datetime(2045, 2, 1)

    # -- two-digit years: 00-68 -> 2000s, 69-99 -> 1900s --
    assert parse_date("1/1/68").year == 2068
    assert parse_date("1/1/69").year == 1969
    assert parse_date("1/1/00").year == 2000
    assert parse_date("12/31/99") == datetime(1999, 12, 31)
    assert parse_date("12.12.99").year == 1999

    # -- month names, full or 3-letter, any case, either word order --
    assert parse_date("Jul 4 2026") == datetime(2026, 7, 4)
    assert parse_date("July 4, 2026") == datetime(2026, 7, 4)
    assert parse_date("4 Jul 2026") == datetime(2026, 7, 4)
    assert parse_date("4 july 2026") == datetime(2026, 7, 4)
    assert parse_date("OCTOBER 31 2026") == datetime(2026, 10, 31)
    assert parse_date("31 OCT 2026") == datetime(2026, 10, 31)

    # surrounding / repeated whitespace is tolerated
    assert parse_date("  2026-07-10  ") == datetime(2026, 7, 10)
    assert parse_date("July  4,  2026") == datetime(2026, 7, 4)

    # -- impossible dates and garbage are ValueErrors --
    for bad in (
        "2026-02-30",            # no Feb 30
        "2026-13-01",
        "13/1/2026",             # slash is US: month 13 does not exist
        "32.1.2026",             # dot is EU: day 32 does not exist
        "0/5/2026",
        "Febtember 1 2026",
        "Jan 32 2026",
        "5/6",                   # incomplete
        "2026-07-10T25:00",
        "2026-07-10T10:61",
        "2026-07-10T10:30:00+25:00",
        "not a date",
        "",
    ):
        expect_value_error(parse_date, bad)

    # -- formatting --
    d = datetime(2026, 7, 4)
    assert format_date(d, "iso") == "2026-07-04"
    assert format_date(d, "us") == "7/4/2026"          # US style is unpadded
    assert format_date(d, "eu") == "04.07.2026"        # EU style is padded
    assert format_date(d, "long") == "July 4, 2026"
    assert format_date(datetime(2026, 7, 4, 9, 5, 0), "iso-full") == "2026-07-04T09:05:00"

    # aware datetimes render their offset; UTC renders as Z
    aware = datetime(2026, 7, 4, 9, 5, 0, tzinfo=timezone.utc)
    assert format_date(aware, "iso-full") == "2026-07-04T09:05:00Z"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert format_date(datetime(2026, 1, 2, 3, 4, 5, tzinfo=ist), "iso-full") \
        == "2026-01-02T03:04:05+05:30"
    pst = timezone(timedelta(hours=-8))
    assert format_date(datetime(2026, 1, 2, 3, 4, 5, tzinfo=pst), "iso-full") \
        == "2026-01-02T03:04:05-08:00"

    expect_value_error(format_date, d, "rfc9999")

    # -- round trips --
    d = datetime(2026, 11, 3)
    for style in ("iso", "us", "eu", "long"):
        assert parse_date(format_date(d, style)) == d, style
    aware = datetime(2026, 11, 3, 16, 45, 10, tzinfo=timezone(timedelta(hours=-5)))
    assert parse_date(format_date(aware, "iso-full")) == aware

    print("ok")


if __name__ == "__main__":
    main()
