"""Cart pricing for the storefront checkout.

All money is integer cents — floats never touch a price. Items are keyed
by SKU; adding the same SKU again accumulates quantity.
"""


def format_cents(cents):
    """Dollar string for a non-negative amount of cents: 1234 -> '$12.34'."""
    if cents < 0:
        raise ValueError("negative amount")
    return "$%d.%02d" % divmod(cents, 100)


class Cart:
    def __init__(self):
        self._items = {}   # sku -> {"name", "unit_cents", "qty"}
        self._order = []   # skus in first-added order

    def add_item(self, sku, name, unit_cents, qty=1):
        if not isinstance(unit_cents, int) or unit_cents < 0:
            raise ValueError("unit_cents must be a non-negative int")
        if not isinstance(qty, int) or qty < 1:
            raise ValueError("qty must be an int >= 1")
        if sku in self._items:
            item = self._items[sku]
            if item["name"] != name or item["unit_cents"] != unit_cents:
                raise ValueError("sku %r already in cart with different details" % sku)
            item["qty"] += qty
        else:
            self._items[sku] = {"name": name, "unit_cents": unit_cents, "qty": qty}
            self._order.append(sku)

    def remove_item(self, sku):
        del self._items[sku]  # KeyError propagates for unknown skus
        self._order.remove(sku)

    def item_count(self):
        return sum(item["qty"] for item in self._items.values())

    def lines(self):
        """(sku, name, unit_cents, qty) tuples in first-added order."""
        return [
            (sku, self._items[sku]["name"], self._items[sku]["unit_cents"],
             self._items[sku]["qty"])
            for sku in self._order
        ]

    def subtotal_cents(self):
        return sum(item["unit_cents"] * item["qty"]
                   for item in self._items.values())

    def total_cents(self):
        """What the customer pays."""
        return self.subtotal_cents()

    def receipt(self):
        """One line per cart line plus the total, ready for the terminal."""
        out = []
        for sku, name, unit_cents, qty in self.lines():
            out.append("%-4s %-18s %2d x %8s" % (sku, name, qty,
                                                 format_cents(unit_cents)))
        out.append("TOTAL %s" % format_cents(self.total_cents()))
        return "\n".join(out)
