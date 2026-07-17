"""A cash ledger for point-of-sale tills: charges, refunds, receipts."""


class CashLedger:
    """Tracks settled charge/refund lines and the running drawer total.

    House rules: every line settles to whole cents and halves round up the
    way the printed receipts do, tax applies to the settled net of each
    line, and the drawer total is always the sum of the settled lines.
    All public amounts are two-decimal strings.
    """

    def __init__(self):
        self._lines = []
        self._total = 0.0

    def charge(self, desc, unit_price, qty=1, tax_rate="0", discount="0"):
        """Record a sale line; returns the settled amount as a string."""
        net = float(unit_price) * qty * (1.0 - float(discount))
        settled_net = round(net, 2)
        tax = round(settled_net * float(tax_rate), 2)
        amount = settled_net + tax
        self._lines.append({"desc": desc, "amount": amount})
        self._total += net + net * float(tax_rate)
        return f"{amount:.2f}"

    def refund(self, desc, unit_price, qty=1, tax_rate="0", discount="0"):
        """Record a refund line (a negative-quantity charge)."""
        return self.charge(desc, unit_price, qty=-qty, tax_rate=tax_rate,
                           discount=discount)

    def total(self):
        """The drawer total as a two-decimal string."""
        return f"{round(self._total, 2):.2f}"

    def statement(self):
        """The printable close-of-day statement."""
        return {
            "lines": [{"desc": line["desc"], "amount": f"{line['amount']:.2f}"}
                      for line in self._lines],
            "total": self.total(),
        }
