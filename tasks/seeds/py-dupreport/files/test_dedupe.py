"""Acceptance suite for dedupe.py -- near-duplicate report grouping.

Pinned behavior:
- normalize(text): casefold, every non-alphanumeric character is a token
  separator, tokens kept in original order, returned as a tuple.
- same(a, b): True iff normalize(a) == normalize(b).
- dedupe(reports, on_compare=None) -> (keepers, groups)
    keepers: indices of first occurrences, in input order
    groups:  {keeper_index: [indices of its later duplicates, input order]};
             keepers without duplicates do not appear
    on_compare: optional zero-arg hook, invoked once per pairwise
    near-duplicate check the implementation performs between two texts.

Scale gate arithmetic (perf-seed policy: document the margin):
  n = 80_000 reports drawn from ~3_900 distinct normalized signatures.
  Pairwise-check budget: 3n = 240_000 (an upper bound; an implementation
  that indexes reports by their normalized form needs 0..n checks and
  finishes this whole file in a few seconds here).
  Comparing each report against the kept representatives needs about
  n * U / 2 = 80_000 * 3_900 / 2 = 1.56e8 checks -- roughly 650x over
  budget -- so the budget hook aborts that shape almost immediately
  instead of letting it grind.

Run: python3 test_dedupe.py
"""

from dedupe import dedupe, normalize, same


class LCG:
    """Deterministic PRNG so the big input is byte-stable across runs."""

    def __init__(self, seed):
        self.state = seed & 0xFFFFFFFF

    def next(self):
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state

    def pick(self, seq):
        return seq[self.next() % len(seq)]


class CompareBudget:
    """Counts on_compare calls; trips as soon as the budget is exceeded so
    a quadratic pass fails fast instead of running for minutes."""

    def __init__(self, limit):
        self.limit = limit
        self.count = 0

    def __call__(self):
        self.count += 1
        if self.count > self.limit:
            raise AssertionError(
                "pairwise-check budget exceeded: more than %d comparisons "
                "-- this workload needs a non-quadratic dedupe pass" % self.limit
            )


def _sig(text):
    """Test-local copy of the canonical rule (independent of the module)."""
    cleaned = "".join(ch if ch.isalnum() else " " for ch in text.casefold())
    return tuple(cleaned.split())


def expected_dedupe(reports):
    """Oracle: first occurrence per signature wins, input order kept."""
    keepers, groups, first = [], {}, {}
    for i, text in enumerate(reports):
        s = _sig(text)
        k = first.get(s)
        if k is None:
            first[s] = i
            keepers.append(i)
        else:
            groups.setdefault(k, []).append(i)
    return keepers, groups


def make_reports(n, rng, faults, areas, codes):
    """Reports with cosmetic noise that never changes the token sequence."""
    seps = [" ", "  ", " - ", ", "]
    prefixes = ["", "  ", "** ", ">> "]
    suffixes = ["", "!!", " ???", " ...", "   "]
    cases = ["asis", "upper", "lower", "title"]
    out = []
    for _ in range(n):
        base = "%s on %s line code %s" % (
            rng.pick(faults), rng.pick(areas), rng.pick(codes))
        style = rng.pick(cases)
        if style == "upper":
            base = base.upper()
        elif style == "lower":
            base = base.lower()
        elif style == "title":
            base = base.title()
        base = base.replace(" ", rng.pick(seps))
        out.append(rng.pick(prefixes) + base + rng.pick(suffixes))
    return out


FAULTS = ["overheat", "jam", "stall", "leak", "vibration", "misfeed",
          "undervoltage", "sensor drift", "belt slip", "door ajar",
          "coolant low", "filter clog", "spindle wobble", "feed skew",
          "torque fault", "encoder gap", "brake drag", "seal wear"]
AREAS = ["press", "conveyor", "chiller", "boiler", "packer", "lift",
         "dock", "mixer", "oven", "labeler", "palletizer", "compressor"]
CODES = ["e%d" % (101 + i) for i in range(18)]


def test_normalize_and_same():
    assert normalize("Re-seat  the O2 sensor!") == ("re", "seat", "the", "o2", "sensor")
    assert normalize("") == ()
    assert normalize("!!! ???") == ()
    assert normalize("A,b;C") == ("a", "b", "c")
    assert same("Chiller overheat!!", "  chiller OVERHEAT ")
    assert same("A b", "a-B")
    assert not same("ab", "a b"), "token boundaries matter"
    assert not same("code e101", "code e102")


def test_dedupe_small():
    reports = [
        "Chiller overheat on line 2!!",     # 0 keeper
        "  chiller OVERHEAT on line 2",     # 1 dup of 0
        "conveyor jam, bay 4",              # 2 keeper
        "Chiller overheat on line 2 ???",   # 3 dup of 0
        "CONVEYOR - JAM - BAY - 4",         # 4 dup of 2
        "conveyor jam bay 5",               # 5 keeper (different token)
        "",                                 # 6 keeper (empty signature)
        "!!!",                              # 7 dup of 6
    ]
    keepers, groups = dedupe(reports)
    assert keepers == [0, 2, 5, 6], keepers
    assert groups == {0: [1, 3], 2: [4], 6: [7]}, groups

    # empty input
    k0, g0 = dedupe([])
    assert k0 == [] and g0 == {}

    # single report
    k1, g1 = dedupe(["only one"])
    assert k1 == [0] and g1 == {}

    # the hook must be optional and must not change the result
    counting = []
    k2, g2 = dedupe(reports, on_compare=lambda: counting.append(1))
    assert (k2, g2) == (keepers, groups), "hook presence changed the result"


def test_dedupe_medium_matches_oracle():
    rng = LCG(1234)
    reports = make_reports(300, rng, FAULTS[:4], AREAS[:5], CODES[:2])
    keepers, groups = dedupe(reports)
    exp_k, exp_g = expected_dedupe(reports)
    assert keepers == exp_k, "keeper set/order diverged from first-occurrence rule"
    assert groups == exp_g, "duplicate grouping diverged"
    # order stability: every duplicate index is greater than its keeper's
    for k, dups in groups.items():
        assert all(d > k for d in dups)
        assert dups == sorted(dups)


def test_dedupe_at_scale_within_budget():
    n = 80_000
    limit = 3 * n
    rng = LCG(987654321)
    reports = make_reports(n, rng, FAULTS, AREAS, CODES)

    budget = CompareBudget(limit)
    keepers, groups = dedupe(reports, on_compare=budget)
    assert budget.count <= limit  # CompareBudget already enforces this

    exp_k, exp_g = expected_dedupe(reports)
    assert keepers == exp_k, "keepers wrong at scale"
    assert groups == exp_g, "groups wrong at scale"
    assert len(keepers) == len({_sig(t) for t in reports})


def main():
    test_normalize_and_same()
    test_dedupe_small()
    test_dedupe_medium_matches_oracle()
    test_dedupe_at_scale_within_budget()
    print("ok")


if __name__ == "__main__":
    main()
