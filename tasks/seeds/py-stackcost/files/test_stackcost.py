"""Acceptance suite for stackcost.py -- deployment-stack cost rollup.

Pinned behavior:
- cost(stack) = unit_cents + sum(count * cost(child)) over its parts;
  exact ints; a template reached along two paths is paid once per path.
- estimate(catalog, root, on_expand=None): on_expand(name) fires each time
  a stack's cost is actually computed; handing back an already-computed
  result must not fire it.
- UnknownStack for missing names, CycleError when expansion re-enters a
  stack already being expanded; both messages name a stack.

Scale gate arithmetic (perf-seed policy: document the margin):
  The rung ladder below is 241 diamonds deep (723 stacks). Expanding every
  path re-expands each shared rung once per path: ~3 * 2^240 ≈ 5e72
  expansions, i.e. ~1e67x over the 500_000-expansion budget, so the budget
  hook trips a path-expanding pass in a couple of seconds. A pass that
  computes each stack once needs at most 723 expansions (the wide catalog
  needs ~3_000) -- hundreds of times under budget -- and this whole file
  then finishes in well under a minute (a few seconds on this class of
  machine). The tracemalloc cap is 64 MiB against an expected peak well
  under 5 MiB: generous headroom, but it catches materializing the
  exponential path expansion in memory.

Run: python3 test_stackcost.py
"""

import tracemalloc

from stackcost import CycleError, UnknownStack, estimate


class LCG:
    """Deterministic PRNG so generated catalogs are identical every run."""

    def __init__(self, seed):
        self.state = seed & 0xFFFFFFFF

    def next(self):
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state

    def below(self, n):
        return self.next() % n


class ExpandBudget:
    """Counts on_expand calls; trips the moment the budget is exceeded so
    an exponential expansion fails fast instead of never finishing."""

    def __init__(self, limit):
        self.limit = limit
        self.count = 0

    def __call__(self, name):
        self.count += 1
        if self.count > self.limit:
            raise AssertionError(
                "expansion budget exceeded: more than %d expansions -- "
                "shared parts are being re-expanded once per path" % self.limit
            )


def oracle_cost(catalog, root):
    """Test-local oracle: iterative post-order rollup (independent of the
    module). Assumes the catalog is valid and acyclic."""
    done = {}
    stack = [(root, False)]
    while stack:
        name, ready = stack.pop()
        if ready:
            spec = catalog[name]
            done[name] = spec["unit_cents"] + sum(
                count * done[child] for child, count in spec["parts"])
            continue
        if name in done:
            continue
        stack.append((name, True))
        for child, _count in catalog[name]["parts"]:
            if child not in done:
                stack.append((child, False))
    return done[root]


def make_wide_catalog(levels, per_level, rng):
    """A layered DAG: node nL_i links only to nodes 1-3 levels deeper, so
    the graph is acyclic and at most `levels` deep, but the number of
    root-to-leaf paths grows exponentially with `levels`."""
    catalog = {}
    for lv in range(levels):
        for i in range(per_level):
            name = "n%d_%d" % (lv, i)
            parts = []
            if lv < levels - 1:
                for _ in range(1 + rng.below(3)):
                    child_lv = min(lv + 1 + rng.below(3), levels - 1)
                    child = "n%d_%d" % (child_lv, rng.below(per_level))
                    parts.append((child, 1 + rng.below(4)))
            catalog[name] = {"unit_cents": 1 + rng.below(5000), "parts": parts}
    return catalog


def make_ladder(depth, rng):
    """Diamond ladder: rung_i -> left_i & right_i -> rung_{i+1}. Every rung
    below the top is shared by exactly two paths from the rung above."""
    catalog = {}
    for i in range(depth):
        catalog["rung%d" % i] = {
            "unit_cents": 1 + rng.below(900),
            "parts": [("left%d" % i, 1), ("right%d" % i, 1)],
        }
        catalog["left%d" % i] = {
            "unit_cents": 1 + rng.below(900),
            "parts": [("rung%d" % (i + 1), 1)],
        }
        catalog["right%d" % i] = {
            "unit_cents": 1 + rng.below(900),
            "parts": [("rung%d" % (i + 1), 2)],
        }
    catalog["rung%d" % depth] = {"unit_cents": 5, "parts": []}
    return catalog


def test_semantics_small():
    catalog = {
        "app":   {"unit_cents": 500, "parts": [("db", 2), ("cache", 1)]},
        "db":    {"unit_cents": 300, "parts": [("disk", 4)]},
        "cache": {"unit_cents": 200, "parts": []},
        "disk":  {"unit_cents": 25, "parts": []},
    }
    assert estimate(catalog, "disk") == 25
    assert estimate(catalog, "db") == 300 + 4 * 25
    assert estimate(catalog, "app") == 500 + 2 * 400 + 200

    # path counting through a diamond: base is paid 1x via left, 3x via right
    diamond = {
        "top":   {"unit_cents": 10, "parts": [("left", 1), ("right", 1)]},
        "left":  {"unit_cents": 1, "parts": [("base", 1)]},
        "right": {"unit_cents": 2, "parts": [("base", 3)]},
        "base":  {"unit_cents": 7, "parts": []},
    }
    assert estimate(diamond, "left") == 8
    assert estimate(diamond, "right") == 23
    assert estimate(diamond, "top") == 41

    # the hook is optional, sees every stack that gets computed, and must
    # not change the result
    seen = []
    assert estimate(diamond, "top", on_expand=seen.append) == 41
    assert set(seen) >= {"top", "left", "right", "base"}


def test_errors():
    def expect(exc, fn):
        try:
            fn()
        except exc as e:
            return str(e)
        raise AssertionError("expected %s" % exc.__name__)

    msg = expect(UnknownStack, lambda: estimate({}, "ghost"))
    assert "ghost" in msg
    catalog = {"a": {"unit_cents": 1, "parts": [("missing", 2)]}}
    msg = expect(UnknownStack, lambda: estimate(catalog, "a"))
    assert "missing" in msg
    assert issubclass(UnknownStack, ValueError)

    loop = {
        "a": {"unit_cents": 1, "parts": [("b", 1)]},
        "b": {"unit_cents": 1, "parts": [("a", 1)]},
    }
    msg = expect(CycleError, lambda: estimate(loop, "a"))
    assert ("'a'" in msg) or ("'b'" in msg)
    assert issubclass(CycleError, ValueError)

    selfloop = {"s": {"unit_cents": 1, "parts": [("s", 1)]}}
    msg = expect(CycleError, lambda: estimate(selfloop, "s"))
    assert "'s'" in msg


def test_medium_matches_oracle():
    # Small enough that even a per-path expansion finishes instantly, so
    # this pins semantics without touching the perf gate.
    rng = LCG(2024)
    catalog = make_wide_catalog(8, 25, rng)
    expected = oracle_cost(catalog, "n0_0")
    assert estimate(catalog, "n0_0") == expected
    counted = []
    assert estimate(catalog, "n0_0", on_expand=counted.append) == expected
    assert len(counted) >= 1


def test_deep_ladder_within_budget():
    rng = LCG(77)
    depth = 240
    catalog = make_ladder(depth, rng)
    budget = ExpandBudget(500_000)
    got = estimate(catalog, "rung0", on_expand=budget)
    assert budget.count <= 500_000
    assert got == oracle_cost(catalog, "rung0"), "ladder cost wrong"


def test_wide_catalog_within_budget_and_memory():
    rng = LCG(424242)
    catalog = make_wide_catalog(60, 50, rng)
    expected = oracle_cost(catalog, "n0_0")

    tracemalloc.start()
    budget = ExpandBudget(500_000)
    got = estimate(catalog, "n0_0", on_expand=budget)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert got == expected, "wide-catalog cost wrong"
    assert budget.count <= 500_000
    # Expected peak is well under 5 MiB; 64 MiB only trips if the
    # expansion itself is being materialized per path.
    assert peak < 64 * 1024 * 1024, "peak memory %d bytes over the 64 MiB cap" % peak


def main():
    test_semantics_small()
    test_errors()
    test_medium_matches_oracle()
    test_deep_ladder_within_budget()
    test_wide_catalog_within_budget_and_memory()
    print("ok")


if __name__ == "__main__":
    main()
