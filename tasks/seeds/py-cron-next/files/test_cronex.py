"""Acceptance tests for the cron expression engine. Run: python3 test_cronex.py"""
from datetime import datetime


def dt(s):
    return datetime.fromisoformat(s)


def main():
    from cronex import parse

    # -- parse returns a schedule; every-minute wildcard --
    sched = parse("* * * * *")
    assert sched.matches(dt("2026-03-05T10:30:00"))
    assert sched.matches(dt("2026-03-05T10:30:00").replace(second=45))
    # next_run is strictly after `after`, at whole-minute resolution
    assert sched.next_run(dt("2026-03-05T10:30:15")) == dt("2026-03-05T10:31:00")
    assert sched.next_run(dt("2026-03-05T10:30:00")) == dt("2026-03-05T10:31:00")
    assert sched.next_run(dt("2026-03-05T23:59:30")) == dt("2026-03-06T00:00:00")

    # -- fixed minute/hour --
    sched = parse("30 14 * * *")
    assert sched.matches(dt("2026-03-05T14:30:00"))
    assert not sched.matches(dt("2026-03-05T14:31:00"))
    assert not sched.matches(dt("2026-03-05T15:30:00"))
    assert sched.next_run(dt("2026-03-05T09:00:00")) == dt("2026-03-05T14:30:00")
    assert sched.next_run(dt("2026-03-05T14:30:00")) == dt("2026-03-06T14:30:00")
    assert sched.next_run(dt("2026-03-05T15:00:00")) == dt("2026-03-06T14:30:00")

    # -- steps on a wildcard --
    sched = parse("*/15 * * * *")
    for m in (0, 15, 30, 45):
        assert sched.matches(dt("2026-03-05T08:00:00").replace(minute=m)), m
    assert not sched.matches(dt("2026-03-05T08:05:00"))
    assert sched.next_run(dt("2026-03-05T10:16:00")) == dt("2026-03-05T10:30:00")
    assert sched.next_run(dt("2026-03-05T10:45:00")) == dt("2026-03-05T11:00:00")

    # -- steps on a range --
    sched = parse("10-40/10 * * * *")
    hits = [m for m in range(60)
            if sched.matches(dt("2026-03-05T08:00:00").replace(minute=m))]
    assert hits == [10, 20, 30, 40], hits
    assert sched.next_run(dt("2026-03-05T10:41:00")) == dt("2026-03-05T11:10:00")

    # -- lists, and ranges in the day-of-week field --
    sched = parse("0 9,17 * * 1-5")          # business hours, weekdays only
    assert sched.matches(dt("2026-03-05T09:00:00"))   # Thursday
    assert sched.matches(dt("2026-03-05T17:00:00"))
    assert not sched.matches(dt("2026-03-05T12:00:00"))
    assert not sched.matches(dt("2026-03-08T09:00:00"))  # Sunday
    # Friday 17:30 -> Monday 09:00
    assert sched.next_run(dt("2026-03-06T17:30:00")) == dt("2026-03-09T09:00:00")

    # -- lists may mix atoms, ranges and steps --
    sched = parse("5,20-22,*/30 * * * *")
    hits = [m for m in range(60)
            if sched.matches(dt("2026-03-05T08:00:00").replace(minute=m))]
    assert hits == [0, 5, 20, 21, 22, 30], hits

    # -- month and weekday names, case-insensitive --
    sched = parse("0 0 1 jan *")
    assert sched.next_run(dt("2026-06-15T00:00:00")) == dt("2027-01-01T00:00:00")
    sched = parse("0 12 * * MON")
    assert sched.matches(dt("2026-03-09T12:00:00"))   # a Monday
    assert not sched.matches(dt("2026-03-10T12:00:00"))
    sched = parse("0 0 * Feb-Mar sat,SUN")
    assert sched.matches(dt("2026-03-01T00:00:00"))   # Sunday in March
    assert not sched.matches(dt("2026-04-05T00:00:00"))  # Sunday, wrong month

    # -- day-of-week 7 is Sunday, same as 0 --
    sched = parse("0 0 * * 7")
    assert sched.matches(dt("2026-03-08T00:00:00"))   # Sunday
    assert sched.next_run(dt("2026-03-05T01:00:00")) == dt("2026-03-08T00:00:00")
    sched0 = parse("0 0 * * 0")
    assert sched0.matches(dt("2026-03-08T00:00:00"))

    # -- when BOTH day-of-month and day-of-week are restricted, cron ORs them --
    sched = parse("0 0 13 * 5")               # the 13th, or any Friday
    assert sched.next_run(dt("2026-03-01T00:00:00")) == dt("2026-03-06T00:00:00")
    assert sched.next_run(dt("2026-03-06T00:00:00")) == dt("2026-03-13T00:00:00")
    assert sched.matches(dt("2026-03-13T00:00:00"))   # Friday the 13th
    assert sched.matches(dt("2026-04-13T00:00:00"))   # a Monday, but the 13th
    assert sched.matches(dt("2026-03-20T00:00:00"))   # a Friday, not the 13th
    assert not sched.matches(dt("2026-03-12T00:00:00"))

    # -- restricted day-of-month with wildcard day-of-week: plain AND --
    sched = parse("0 0 31 * *")
    assert sched.next_run(dt("2026-04-15T00:00:00")) == dt("2026-05-31T00:00:00")

    # -- schedules that only exist some years still resolve --
    sched = parse("0 0 29 2 *")
    assert sched.next_run(dt("2026-03-01T00:00:00")) == dt("2028-02-29T00:00:00")

    # -- rejects malformed expressions --
    bad = [
        "* * * *",              # four fields
        "* * * * * *",          # six fields
        "",                     # empty
        "60 * * * *",           # minute out of range
        "* 24 * * *",           # hour out of range
        "* * 0 * *",            # day-of-month starts at 1
        "* * 32 * *",
        "* * * 13 *",           # month out of range
        "* * * 0 *",
        "* * * * 8",            # day-of-week tops out at 7
        "*/0 * * * *",          # zero step
        "10-40/0 * * * *",
        "5-1 * * * *",          # reversed range
        "* * * feb-jan *",      # reversed name range
        "1,,2 * * * *",         # empty list item
        "* * * bogus *",        # unknown name
        "a * * * *",
    ]
    for expr in bad:
        try:
            parse(expr)
            assert False, f"parse({expr!r}) should raise ValueError"
        except ValueError:
            pass

    print("ok")


if __name__ == "__main__":
    main()
