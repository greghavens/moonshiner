"""Acceptance checks for ledger.py. Run: python3 test_ledger.py"""
from ledger import Ledger


def make_ledger():
    lg = Ledger()
    lg.open_account("checking")
    lg.open_account("savings")
    lg.post("2026-03-05", "checking", 50000, "salary")
    lg.post("2026-01-10", "checking", 20000, "opening")  # backdated
    lg.post("2026-02-14", "checking", -4500, "dinner")
    lg.post("2026-03-05", "savings", 10000, "transfer")
    lg.post("2026-01-31", "checking", -1500, "gym")      # backdated
    return lg


# ---------------------------------------------------------------- existing

def test_open_account_rules():
    lg = Ledger()
    lg.open_account("checking")
    lg.open_account("savings")
    assert lg.accounts() == ["checking", "savings"]
    try:
        lg.open_account("checking")
        assert False, "duplicate account opened"
    except ValueError:
        pass
    try:
        lg.open_account("  ")
        assert False, "blank account name accepted"
    except ValueError:
        pass


def test_post_validation():
    lg = Ledger()
    lg.open_account("checking")
    try:
        lg.post("2026-01-01", "brokerage", 100)
        assert False, "posted to unknown account"
    except KeyError:
        pass
    for amount in [0, 10.5, True]:
        try:
            lg.post("2026-01-01", "checking", amount)
            assert False, "accepted amount %r" % (amount,)
        except ValueError:
            pass
    for bad_date in ["not-a-date", "2026-02-30", "01/02/2026"]:
        try:
            lg.post(bad_date, "checking", 100)
            assert False, "accepted date %r" % (bad_date,)
        except ValueError:
            pass


def test_balances():
    lg = make_ledger()
    assert lg.balance("checking") == 64000
    assert lg.balance("savings") == 10000
    assert lg.balances() == {"checking": 64000, "savings": 10000}
    lg.open_account("escrow")
    assert lg.balances()["escrow"] == 0
    try:
        lg.balance("brokerage")
        assert False, "balance of unknown account"
    except KeyError:
        pass


def test_history_is_date_ordered():
    lg = make_ledger()
    memos = [e.memo for e in lg.history("checking")]
    assert memos == ["opening", "gym", "dinner", "salary"]
    lg.post("2026-03-05", "checking", -100, "coffee")
    memos = [e.memo for e in lg.history("checking")]
    assert memos[-2:] == ["salary", "coffee"]  # same day keeps post order


def test_post_returns_entry():
    lg = Ledger()
    lg.open_account("checking")
    e = lg.post("2026-05-01", "checking", -2500, "book")
    assert (e.account, e.amount, e.memo) == ("checking", -2500, "book")
    assert e.date.isoformat() == "2026-05-01"


# --------------------------------- feature: as-of (point-in-time) queries

def test_balance_asof_includes_the_day_itself():
    lg = make_ledger()
    assert lg.balance_asof("checking", "2026-01-31") == 18500
    assert lg.balance_asof("checking", "2026-01-30") == 20000


def test_balance_asof_ignores_posting_order():
    lg = make_ledger()
    # salary was POSTED first but is dated 2026-03-05; backdated entries
    # dated earlier must be the only ones counted here.
    assert lg.balance_asof("checking", "2026-02-28") == 14000
    assert lg.balance_asof("savings", "2026-03-04") == 0
    assert lg.balance_asof("savings", "2026-03-05") == 10000


def test_balance_asof_boundaries():
    lg = make_ledger()
    assert lg.balance_asof("checking", "2025-12-31") == 0
    assert lg.balance_asof("checking", "2026-12-31") == lg.balance("checking")
    lg.post("2026-04-01", "checking", -100, "fee a")
    lg.post("2026-04-01", "checking", -100, "fee b")
    assert lg.balance_asof("checking", "2026-04-01") == 64000 - 200


def test_balances_asof_covers_every_account():
    lg = make_ledger()
    assert lg.balances_asof("2026-02-01") == {"checking": 18500, "savings": 0}
    assert lg.balances_asof("2026-03-05") == {"checking": 64000,
                                              "savings": 10000}


def test_asof_validation():
    lg = make_ledger()
    try:
        lg.balance_asof("brokerage", "2026-01-01")
        assert False, "as-of balance of unknown account"
    except KeyError:
        pass
    for bad in ["02/01/2026", "2026-13-01", "", None]:
        try:
            lg.balance_asof("checking", bad)
            assert False, "accepted as-of date %r" % (bad,)
        except ValueError:
            pass
        try:
            lg.balances_asof(bad)
            assert False, "balances_asof accepted %r" % (bad,)
        except ValueError:
            pass


EXISTING = [
    test_open_account_rules,
    test_post_validation,
    test_balances,
    test_history_is_date_ordered,
    test_post_returns_entry,
]

FEATURE = [
    test_balance_asof_includes_the_day_itself,
    test_balance_asof_ignores_posting_order,
    test_balance_asof_boundaries,
    test_balances_asof_covers_every_account,
    test_asof_validation,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
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
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
