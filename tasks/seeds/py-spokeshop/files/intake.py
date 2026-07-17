"""Front-desk intake flow: each step stamps the order dict."""

INTAKE_STEPS = {
    "dropoff": step_dropoff,
    "quote": step_quote,
    "approve": step_approve,
}


def step_dropoff(order):
    order["status"] = "checked-in"
    return order


def step_quote(order):
    order["status"] = "quoted"
    return order


def step_approve(order):
    order["status"] = "approved"
    return order


def run_intake(order, steps):
    """Run the named steps in the order given; unknown steps raise KeyError."""
    for name in steps:
        INTAKE_STEPS[name](order)
    return order
