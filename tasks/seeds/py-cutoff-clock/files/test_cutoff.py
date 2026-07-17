from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from cutoff import CutoffCalculator

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
TOKYO = ZoneInfo("Asia/Tokyo")


def check_naive_local_orders():
    calc = CutoffCalculator(NY)
    assert calc.makes_cutoff(datetime(2026, 7, 6, 10, 0)) is True
    assert calc.ship_date(datetime(2026, 7, 6, 10, 0)) == date(2026, 7, 6)
    assert calc.makes_cutoff(datetime(2026, 7, 6, 16, 30)) is True
    assert calc.makes_cutoff(datetime(2026, 7, 6, 16, 30, 1)) is False
    assert calc.ship_date(datetime(2026, 7, 6, 17, 45)) == date(2026, 7, 7)


def check_aware_orders_decide_on_warehouse_clock():
    calc = CutoffCalculator(NY)
    # 19:00Z on Jul 6 is 15:00 in New York: inside the window.
    assert calc.makes_cutoff(datetime(2026, 7, 6, 19, 0, tzinfo=UTC)) is True
    assert calc.ship_date(datetime(2026, 7, 6, 19, 0, tzinfo=UTC)) == date(2026, 7, 6)
    # 03:30Z on Jul 7 is still 23:30 on Jul 6 in New York: missed that pickup.
    late = datetime(2026, 7, 7, 3, 30, tzinfo=UTC)
    assert calc.makes_cutoff(late) is False
    assert calc.ship_date(late) == date(2026, 7, 7)
    # An order stamped Jul 7 05:00 in Tokyo is Jul 6 16:00 in New York.
    jst = datetime(2026, 7, 7, 5, 0, tzinfo=TOKYO)
    assert calc.makes_cutoff(jst) is True
    assert calc.ship_date(jst) == date(2026, 7, 6)


def check_deadline_is_zone_aware():
    calc = CutoffCalculator(NY)
    d = calc.deadline_for(datetime(2026, 7, 6, 19, 0, tzinfo=UTC))
    assert d.tzinfo is not None
    assert d.utcoffset() == timedelta(hours=-4)
    assert (d.year, d.month, d.day, d.hour, d.minute) == (2026, 7, 6, 16, 30)
    w = calc.deadline_for(datetime(2026, 1, 15, 18, 0, tzinfo=UTC))
    assert w.utcoffset() == timedelta(hours=-5)
    assert (w.month, w.day, w.hour, w.minute) == (1, 15, 16, 30)
    # The deadline sits on the warehouse-local day even when UTC has moved on.
    d2 = calc.deadline_for(datetime(2026, 7, 7, 3, 30, tzinfo=UTC))
    assert (d2.month, d2.day) == (7, 6)


def check_spring_forward_day():
    calc = CutoffCalculator(NY)
    a = datetime(2026, 3, 8, 20, 29, tzinfo=UTC)  # 16:29 in New York
    b = datetime(2026, 3, 8, 20, 31, tzinfo=UTC)  # 16:31 in New York
    assert calc.makes_cutoff(a) is True
    assert calc.ship_date(a) == date(2026, 3, 8)
    assert calc.makes_cutoff(b) is False
    assert calc.ship_date(b) == date(2026, 3, 9)
    assert calc.deadline_for(a).utcoffset() == timedelta(hours=-4)


def check_fall_back_fold():
    calc = CutoffCalculator(NY)
    first = calc.normalize(datetime(2026, 11, 1, 1, 30))
    second = calc.normalize(datetime(2026, 11, 1, 1, 30, fold=1))
    assert first.utcoffset() == timedelta(hours=-4)
    assert second.utcoffset() == timedelta(hours=-5)
    for ts in (first, second):
        assert calc.makes_cutoff(ts) is True
        assert calc.ship_date(ts) == date(2026, 11, 1)
    # The same repeated wall time arriving as unambiguous UTC instants.
    early = calc.normalize(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    late = calc.normalize(datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert (early.hour, early.minute) == (1, 30)
    assert (late.hour, late.minute) == (1, 30)
    assert early.utcoffset() == timedelta(hours=-4)
    assert late.utcoffset() == timedelta(hours=-5)
    # An hour of real time passed between the two, fold notwithstanding.
    assert late.astimezone(UTC) - early.astimezone(UTC) == timedelta(hours=1)


def check_status_payload():
    calc = CutoffCalculator(NY)
    s = calc.status(datetime(2026, 7, 6, 19, 0, tzinfo=UTC))
    assert s["same_day"] is True
    assert s["ships_on"] == "2026-07-06"
    assert s["deadline"] == "2026-07-06T16:30:00-04:00"


def main():
    check_naive_local_orders()
    check_aware_orders_decide_on_warehouse_clock()
    check_deadline_is_zone_aware()
    check_spring_forward_day()
    check_fall_back_fold()
    check_status_payload()
    print("all cutoff tests passed")


if __name__ == "__main__":
    main()
