"""Menu pricing for the catering order desk.

Supplier costs arrive as (sku, qty, unit_cost) rows off the weekly
sheet; the front desk quotes plates with the house margin applied and
adds the service charge on party orders.
"""

MARGIN = 0.35
SERVICE_PCT = 12.5


def plate_costs(rows):
    """Raw plate cost per sku: qty * unit cost, summed over duplicate rows."""
    costs = {}
    for row in rows:
        sku = row["sku"]
        costs[sku] = costs.get(sku, 0.0) + row["qty"] * row["unit_cost"]
    return costs


def menu_prices(rows):
    """Quoted price per sku: plate cost with the house margin, in cents-rounded dollars."""
    costs = plate_costs(rows)
    return {sku: round(cost * (1 + MARGIN, 2)
            for sku, cost in costs.items()}


def quote(rows, sku):
    """Quoted price for one sku; unknown skus are a KeyError on purpose."""
    return menu_prices(rows)[sku]


def party_subtotal(rows, skus):
    """Subtotal for a party order: one plate per requested sku."""
    prices = menu_prices(rows)
    return round(sum(prices[s] for s in skus), 2)


def party_total(rows, skus):
    """Party subtotal plus the service charge."""
    return round(party_subtotal(rows, skus) * (1 + SERVICE_PCT / 100), 2)
