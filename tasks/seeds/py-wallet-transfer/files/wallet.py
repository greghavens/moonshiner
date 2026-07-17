"""Wallet ledger for the payments service.

Each customer wallet carries its own lock so unrelated transfers can run
in parallel on the worker pool. transfer() debits the source and credits
the destination atomically with respect to any other transfer touching
the same wallets. An optional on_debit hook fires while the source hold
is in place — the fraud team subscribes to it to mirror pending holds
into their audit stream before the money lands anywhere else.
"""
import threading


class InsufficientFunds(RuntimeError):
    """Source wallet does not cover the requested amount."""


class Wallet:
    def __init__(self, wallet_id, balance=0):
        self.wallet_id = wallet_id
        self.balance = balance
        self.lock = threading.Lock()

    def __repr__(self):
        return f"Wallet({self.wallet_id!r}, balance={self.balance})"


class LedgerService:
    def __init__(self, on_debit=None):
        self._on_debit = on_debit
        self.completed = []

    def transfer(self, src, dst, amount):
        """Move *amount* from src to dst; records the transfer on success."""
        if amount <= 0:
            raise ValueError("amount must be positive")
        if src.wallet_id == dst.wallet_id:
            raise ValueError("cannot transfer a wallet to itself")
        with src.lock:
            if src.balance < amount:
                raise InsufficientFunds(
                    f"{src.wallet_id} holds {src.balance}, needs {amount}")
            src.balance -= amount
            if self._on_debit is not None:
                self._on_debit(src.wallet_id, dst.wallet_id, amount)
            with dst.lock:
                dst.balance += amount
        self.completed.append((src.wallet_id, dst.wallet_id, amount))

    def total_moved(self):
        """Sum of all amounts successfully transferred so far."""
        return sum(amount for _, _, amount in self.completed)
