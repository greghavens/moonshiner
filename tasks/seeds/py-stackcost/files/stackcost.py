"""Deployment-stack cost rollup.

A catalog maps stack names to templates:

    catalog = {
        "web-tier": {"unit_cents": 1200, "parts": [("vm-small", 4), ("lb", 1)]},
        "vm-small": {"unit_cents": 900, "parts": []},
        "lb":       {"unit_cents": 2500, "parts": []},
    }

Instantiating a stack instantiates every part afresh: the cost of a stack
is its own unit_cents plus count * cost(child) for every (child, count)
in its parts list. A template reachable along two different paths is paid
for once per path (multiplied through the counts along each path). Costs
are exact integers (cents) -- no floats anywhere.

estimate(catalog, root, on_expand=None) -> int
    on_expand(name), when given, must be called each time a stack's cost
    is actually computed (expanded). Handing back an already-computed
    result without recomputation must NOT call the hook -- the perf suite
    budgets expansions through it.

Errors (both subclass ValueError and name the offending stack):
    UnknownStack -- root or any referenced part is missing from the catalog
    CycleError   -- the parts graph reaches a stack already being expanded
"""


class UnknownStack(ValueError):
    """A stack name that does not exist in the catalog."""


class CycleError(ValueError):
    """The parts graph loops back on itself."""


def estimate(catalog, root, on_expand=None):
    """Total cost in cents of instantiating one ``root`` stack."""

    def cost(name, trail):
        if name not in catalog:
            raise UnknownStack("unknown stack: %r" % (name,))
        if name in trail:
            raise CycleError("cycle through %r" % (name,))
        if on_expand is not None:
            on_expand(name)
        spec = catalog[name]
        total = spec["unit_cents"]
        for child, count in spec["parts"]:
            total += count * cost(child, trail | {name})
        return total

    return cost(root, frozenset())
