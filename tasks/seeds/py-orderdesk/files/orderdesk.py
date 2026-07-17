"""orderdesk.py — pricing, tax, shipping and reporting for wholesale orders.

The order desk tools (CLI, nightly invoice batch, the ops notebook) all
import this module. An order is a plain dict:

    {
        "id": 2041,
        "customer": {"name": "Cedar Grocery Co-op", "tax_exempt": False},
        "lines": [
            {"sku": "RICE-25KG", "category": "food",
             "unit_cents": 4500, "qty": 4, "weight_g": 25000},
            ...
        ],
    }

All money is integer cents; all arithmetic stays in integers so the
invoice batch is reproducible to the cent.
"""

# ---------------------------------------------------------------- pricing

# Quantity breaks, checked top down: buy at least `min_qty` of a line and
# the whole line gets `percent` off. Only the first matching break applies.
PRICE_BREAKS = [
    (50, 10),  # pallet pricing
    (10, 5),   # case pricing
]


def line_total_cents(line):
    """Extended line total after the quantity break, floored to a cent."""
    base = line["unit_cents"] * line["qty"]
    for min_qty, percent in PRICE_BREAKS:
        if line["qty"] >= min_qty:
            return base * (100 - percent) // 100
    return base


def price_lines(order):
    """[(sku, line_total_cents), ...] in the order the lines appear."""
    return [(line["sku"], line_total_cents(line)) for line in order["lines"]]


def subtotal_cents(order):
    return sum(total for _, total in price_lines(order))


# -------------------------------------------------------------------- tax

# Sales tax in basis points by product category. Categories we do not
# know default to "standard" — new categories show up in the feed before
# anyone tells us about them.
TAX_RATES_BP = {
    "food": 0,
    "books": 0,
    "standard": 825,
}


def tax_cents(order):
    """Tax is computed per line on the discounted line total and floored
    per line — that is how the ledger has always rounded, keep it."""
    if order["customer"].get("tax_exempt"):
        return 0
    total = 0
    for line in order["lines"]:
        rate = TAX_RATES_BP.get(line.get("category", "standard"),
                                TAX_RATES_BP["standard"])
        total += line_total_cents(line) * rate // 10000
    return total


# --------------------------------------------------------------- shipping

# Weight bands: (max total grams inclusive, cents). Heavier than the last
# band pays the overweight flat rate.
SHIPPING_BANDS = [
    (500, 495),
    (2000, 795),
    (10000, 1495),
]
OVERWEIGHT_CENTS = 2495

# Orders at or above this subtotal (after quantity breaks) ship free.
FREE_SHIPPING_MIN_CENTS = 15000


def total_weight_g(order):
    return sum(line["weight_g"] * line["qty"] for line in order["lines"])


def shipping_cents(order):
    if not order["lines"]:
        return 0
    if subtotal_cents(order) >= FREE_SHIPPING_MIN_CENTS:
        return 0
    weight = total_weight_g(order)
    for max_g, cents in SHIPPING_BANDS:
        if weight <= max_g:
            return cents
    return OVERWEIGHT_CENTS


# ----------------------------------------------------------------- totals

def order_total(order):
    subtotal = subtotal_cents(order)
    tax = tax_cents(order)
    shipping = shipping_cents(order)
    return {
        "subtotal_cents": subtotal,
        "tax_cents": tax,
        "shipping_cents": shipping,
        "total_cents": subtotal + tax + shipping,
    }


# ----------------------------------------------------------------- report

def _dollars(cents):
    return "$%d.%02d" % divmod(cents, 100)


def render_report(order):
    """Fixed-width text summary. The fulfillment desk prints these and
    files them, so the layout is load-bearing — do not tweak it."""
    totals = order_total(order)
    out = []
    out.append("ORDER %s — %s" % (order["id"], order["customer"]["name"]))
    out.append("%-12s%4s%12s" % ("SKU", "QTY", "LINE TOTAL"))
    for line in order["lines"]:
        out.append("%-12s%4d%12s"
                   % (line["sku"], line["qty"],
                      _dollars(line_total_cents(line))))
    out.append("-" * 28)
    out.append("%-16s%12s" % ("Subtotal", _dollars(totals["subtotal_cents"])))
    out.append("%-16s%12s" % ("Tax", _dollars(totals["tax_cents"])))
    out.append("%-16s%12s" % ("Shipping", _dollars(totals["shipping_cents"])))
    out.append("%-16s%12s" % ("TOTAL", _dollars(totals["total_cents"])))
    return "\n".join(out) + "\n"
