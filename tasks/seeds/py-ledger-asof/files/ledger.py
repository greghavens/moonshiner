"""Account ledger behind the monthly-close scripts.

Amounts are signed integer cents (deposits positive, withdrawals
negative). Entries carry an ISO date and are kept in posting order; a
sequence number disambiguates same-day entries.
"""

from datetime import date


def _parse_date(value):
    """ISO 'YYYY-MM-DD' -> datetime.date; anything else is a ValueError."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("bad date: %r (want YYYY-MM-DD)" % (value,)) from None


class Entry:
    __slots__ = ("date", "account", "amount", "memo", "seq")

    def __init__(self, when, account, amount, memo, seq):
        self.date = when
        self.account = account
        self.amount = amount
        self.memo = memo
        self.seq = seq

    def __repr__(self):
        return "Entry(%s, %r, %d, %r)" % (self.date.isoformat(), self.account,
                                          self.amount, self.memo)


class Ledger:
    def __init__(self):
        self._accounts = set()
        self._entries = []
        self._seq = 0

    def open_account(self, name):
        if not name or not name.strip():
            raise ValueError("account name required")
        if name in self._accounts:
            raise ValueError("account already open: %r" % (name,))
        self._accounts.add(name)

    def accounts(self):
        return sorted(self._accounts)

    def post(self, when, account, amount, memo=""):
        """Record one entry. Backdated postings are allowed."""
        parsed = _parse_date(when)
        if account not in self._accounts:
            raise KeyError(account)
        if not isinstance(amount, int) or isinstance(amount, bool) or amount == 0:
            raise ValueError("amount must be a nonzero int of cents")
        entry = Entry(parsed, account, amount, memo, self._seq)
        self._seq += 1
        self._entries.append(entry)
        return entry

    def balance(self, account):
        if account not in self._accounts:
            raise KeyError(account)
        return sum(e.amount for e in self._entries if e.account == account)

    def balances(self):
        """Current balance of every open account (0 when never posted)."""
        return {name: self.balance(name) for name in self._accounts}

    def history(self, account):
        """Entries for one account, oldest first; same-day keeps post order."""
        if account not in self._accounts:
            raise KeyError(account)
        mine = [e for e in self._entries if e.account == account]
        return sorted(mine, key=lambda e: (e.date, e.seq))
