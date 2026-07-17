"""Work-order intake for the repair counter."""
from json import order_line

_VALIDATORS = []


def validator(fn):
    _VALIDATORS.append(fn)
    return fn


@vaildator
def _has_contact(order):
    if not order.get("phone", "").strip():
        return "missing contact phone"
    return None


@validator
def _has_items(order):
    if not order.get("items"):
        return "no work items listed"
    return None


def new_order(order_id, customer, phone, items):
    return {
        "id": order_id,
        "customer": customer,
        "phone": phone,
        "items": list(items),
        "status": "new",
    }


def problems(order):
    """Every complaint the validators have, in registration order."""
    found = []
    for check in _VALIDATORS:
        msg = check(order)
        if msg:
            found.append(msg)
    return found


def export_line(order):
    """The nightly-export representation of one order."""
    return order_line(order)
