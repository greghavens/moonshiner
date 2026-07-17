"""Acceptance tests for the repair-counter app. Run: python3 test_spokeshop.py"""


def test_rate_card():
    from pricing import RateCard

    card = RateCard()
    assert card.quote("tune") == 6500
    assert card.quote("flat") == 1200
    assert card.quote("tune", weekend=True) == 8000
    assert card.quote("overhaul", weekend=True) == 19500
    try:
        card.quote("paint")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown jobs must raise KeyError")


def test_tickets():
    from tickets import Ticket, WarrantyTicket

    t = Ticket(101, "2026-07-01")
    assert t.summary() == "#101 opened 2026-07-01 (open)"
    t.close()
    assert t.summary() == "#101 opened 2026-07-01 (closed)"

    w = WarrantyTicket(102, "2026-07-02", "W-77")
    assert w.order_id == 102
    assert w.claim_no == "W-77"
    assert w.summary() == "#102 opened 2026-07-02 (open) [warranty W-77]"


def test_bay_assignment():
    from bays import assign_bay

    # One busy morning: callers thread the same occupancy list through.
    morning = []
    o1, o2, o3, o4 = {}, {}, {}, {}
    assert assign_bay(o1, morning) == "B1" and o1["bay"] == "B1"
    assert assign_bay(o2, morning) == "B2"
    assert assign_bay(o3, morning) == "B3"
    try:
        assign_bay(o4, morning)
    except RuntimeError:
        pass
    else:
        raise AssertionError("a full rack must refuse a fourth order")

    # Separate quiet days: nothing occupied, everyone gets the first stand.
    for _ in range(4):
        order = {}
        assert assign_bay(order) == "B1", "empty shop must hand out B1"
        assert order["bay"] == "B1"


def test_intake_flow():
    from intake import run_intake

    order = {"id": 9, "status": "new"}
    run_intake(order, ["dropoff", "quote"])
    assert order["status"] == "quoted"
    run_intake(order, ["approve"])
    assert order["status"] == "approved"
    try:
        run_intake(order, ["polish"])
    except KeyError:
        pass
    else:
        raise AssertionError("unknown intake steps must raise KeyError")


def test_orders_and_export():
    import orders

    order = orders.new_order(7, "Ana", "555-0101", ["tune"])
    assert orders.problems(order) == []

    bad = orders.new_order(8, "Ben", "  ", [])
    assert orders.problems(bad) == ["missing contact phone", "no work items listed"]

    line = orders.export_line(order)
    assert line == (
        '{"customer":"Ana","id":7,"items":["tune"],'
        '"phone":"555-0101","status":"new"}'
    ), line


def main():
    test_rate_card()
    test_tickets()
    test_bay_assignment()
    test_intake_flow()
    test_orders_and_export()
    print("all spokeshop tests passed")


if __name__ == "__main__":
    main()
