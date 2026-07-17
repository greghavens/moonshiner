"""Behavior checks for the wallet ledger. Run: python3 test_wallet.py"""
import threading

from wallet import InsufficientFunds, LedgerService, Wallet


def test_basic_transfers():
    svc = LedgerService()
    a = Wallet("acct-alice", 100)
    b = Wallet("acct-bob", 50)

    svc.transfer(a, b, 40)
    assert a.balance == 60 and b.balance == 90

    try:
        svc.transfer(b, a, 500)
        raise AssertionError("overdraft must be rejected")
    except InsufficientFunds:
        pass
    assert a.balance == 60 and b.balance == 90, "failed transfer must not move money"

    try:
        svc.transfer(a, b, 0)
        raise AssertionError("zero amount must be rejected")
    except ValueError:
        pass

    assert svc.completed == [("acct-alice", "acct-bob", 40)]
    assert svc.total_moved() == 40


def test_simultaneous_transfers_between_same_wallets():
    # Two customers paying each other back at the same moment — the exact
    # traffic pattern our settlement batch produces every night. The fraud
    # audit hook is subscribed, as it is in production.
    rendezvous = threading.Barrier(2)

    def audit_hook(src_id, dst_id, amount):
        try:
            rendezvous.wait(timeout=2.0)
        except threading.BrokenBarrierError:
            pass

    svc = LedgerService(on_debit=audit_hook)
    alice = Wallet("acct-alice", 100)
    bob = Wallet("acct-bob", 100)

    t1 = threading.Thread(target=svc.transfer, args=(alice, bob, 30), daemon=True)
    t2 = threading.Thread(target=svc.transfer, args=(bob, alice, 10), daemon=True)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert not t1.is_alive() and not t2.is_alive(), (
        "transfers never completed — payment workers are wedged")
    assert alice.balance == 80, f"alice: {alice.balance}"
    assert bob.balance == 120, f"bob: {bob.balance}"
    assert len(svc.completed) == 2 and svc.total_moved() == 40


def main():
    test_basic_transfers()
    test_simultaneous_transfers_between_same_wallets()
    print("all checks passed")


if __name__ == "__main__":
    main()
