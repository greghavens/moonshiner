from decimal import Decimal

from cashledger import CashLedger


def check_half_cent_settles_up():
    led = CashLedger()
    assert led.charge("gum", "0.125") == "0.13"
    assert led.charge("candy-bar", "2.675") == "2.68"
    assert led.charge("mint", "1.005") == "1.01"
    assert led.charge("chip-clip", "0.115") == "0.12"
    assert led.total() == "3.94"


def check_refunds_settle_away_from_zero():
    led = CashLedger()
    assert led.refund("gum-return", "0.125") == "-0.13"
    assert led.refund("case-return", "2.675") == "-2.68"
    assert led.total() == "-2.81"


def check_tax_applies_to_settled_net():
    led = CashLedger()
    assert led.charge("bolt", "1.018", tax_rate="0.25") == "1.28"
    assert led.charge("labor", "39.999", tax_rate="0.05") == "42.00"
    assert led.total() == "43.28"


def check_discounted_taxed_line():
    led = CashLedger()
    assert led.charge("kit", "0.35", qty=3, discount="0.10",
                      tax_rate="0.0825") == "1.03"


def check_no_drift_over_many_lines():
    led = CashLedger()
    for _ in range(40):
        amount = led.charge("kit", "0.35", qty=3, discount="0.10",
                            tax_rate="0.0825")
        assert amount == "1.03"
    s = led.statement()
    assert all(line["amount"] == "1.03" for line in s["lines"])
    assert s["total"] == "41.20"


def check_total_equals_sum_of_lines():
    led = CashLedger()
    led.charge("americano", "3.75", qty=2, tax_rate="0.0825")
    led.refund("stale-scone", "2.95", tax_rate="0.0825")
    for _ in range(100):
        led.charge("card-fee", "0.019")
    s = led.statement()
    line_sum = sum(Decimal(line["amount"]) for line in s["lines"])
    assert s["total"] == f"{line_sum:.2f}"
    assert s["total"] == "6.93"


def check_two_decimal_serialization():
    led = CashLedger()
    assert led.charge("service-call", "7") == "7.00"
    assert led.total() == "7.00"
    s = led.statement()
    assert s == {"lines": [{"desc": "service-call", "amount": "7.00"}],
                 "total": "7.00"}


def check_zero_sum_drawer():
    led = CashLedger()
    led.charge("widget", "19.99", tax_rate="0.07")
    led.refund("widget-return", "19.99", tax_rate="0.07")
    assert led.total() == "0.00"


def main():
    check_half_cent_settles_up()
    check_refunds_settle_away_from_zero()
    check_tax_applies_to_settled_net()
    check_discounted_taxed_line()
    check_no_drift_over_many_lines()
    check_total_equals_sum_of_lines()
    check_two_decimal_serialization()
    check_zero_sum_drawer()
    print("all cashledger tests passed")


if __name__ == "__main__":
    main()
