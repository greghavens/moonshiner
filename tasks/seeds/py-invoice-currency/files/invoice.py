"""Plain-text invoice rendering for the billing service.

Amounts are floats in the invoice currency. Every computed amount is
rounded to cents as soon as it is produced so the numbers printed on an
invoice always add up to the printed total.
"""


class LineItem:
    def __init__(self, description, qty, unit_price):
        if qty <= 0:
            raise ValueError("qty must be positive")
        if unit_price < 0:
            raise ValueError("unit_price cannot be negative")
        self.description = description
        self.qty = qty
        self.unit_price = unit_price

    def total(self):
        return round(self.qty * self.unit_price, 2)


class Invoice:
    def __init__(self, number, customer, tax_rate=0.0):
        if not number:
            raise ValueError("invoice number required")
        self.number = number
        self.customer = customer
        self.tax_rate = tax_rate
        self.items = []

    def add(self, description, qty, unit_price):
        """Append a line item; returns self so calls can be chained."""
        self.items.append(LineItem(description, qty, unit_price))
        return self

    def subtotal(self):
        return round(sum(item.total() for item in self.items), 2)

    def tax(self):
        return round(self.subtotal() * self.tax_rate, 2)

    def total(self):
        return round(self.subtotal() + self.tax(), 2)


def format_money(amount):
    """Format an amount for display, e.g. 1234.5 -> '$1234.50'."""
    return "$%.2f" % amount


def render(invoice, issued="2026-01-01"):
    """Render the invoice as a plain-text block for email/print."""
    lines = [
        "Invoice %s" % invoice.number,
        "Customer: %s" % invoice.customer,
        "Date: %s" % issued,
        "",
    ]
    for item in invoice.items:
        left = "  %-22s %3d x %s" % (item.description, item.qty,
                                     format_money(item.unit_price))
        lines.append("%-44s%12s" % (left, format_money(item.total())))
    lines.append("")
    lines.append("Subtotal: %s" % format_money(invoice.subtotal()))
    if invoice.tax_rate:
        lines.append("Tax: %s" % format_money(invoice.tax()))
    lines.append("Total: %s" % format_money(invoice.total()))
    return "\n".join(lines)
