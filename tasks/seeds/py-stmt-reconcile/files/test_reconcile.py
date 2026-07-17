"""Acceptance checks for reconcile.py. Run: python3 test_reconcile.py"""
import copy

from reconcile import reconcile


def L(id_, date, amount, memo=""):
    return {"id": id_, "date": date, "amount": amount, "memo": memo}


def S(ref, date, amount, desc=""):
    return {"ref": ref, "date": date, "amount": amount, "desc": desc}


def test_march_close_end_to_end():
    ledger = [
        L("e1", "2026-03-01", -4500, "coffee supplies"),
        L("e2", "2026-03-03", -9900, "software"),
        L("e3", "2026-03-10", -70000, "rent part 1"),
        L("e4", "2026-03-10", -50000, "rent part 2"),
        L("e5", "2026-03-20", -1000, "stamps"),
    ]
    statement = [
        S("s1", "2026-03-01", -4500, "CARD 4421 COFFEE"),
        S("s2", "2026-03-05", -9900, "ACME SOFTWARE"),
        S("s3", "2026-03-10", -120000, "TRANSFER LANDLORD"),
        S("s4", "2026-03-25", -777, "MYSTERY FEE"),
    ]
    out = reconcile(ledger, statement)
    assert out["matches"] == [
        {"ref": "s1", "entry_ids": ["e1"], "kind": "exact"},
        {"ref": "s2", "entry_ids": ["e2"], "kind": "date"},
        {"ref": "s3", "entry_ids": ["e3", "e4"], "kind": "split"},
    ], out["matches"]
    assert out["unmatched_ledger"] == ["e5"]
    assert out["unmatched_statement"] == ["s4"]


def test_exact_pass_runs_before_fuzzy_for_every_line():
    # A per-line greedy would give s_early the fuzzy match and orphan the
    # exact one; the pass structure must not.
    ledger = [L("e1", "2026-04-07", 10000)]
    statement = [S("sA", "2026-04-05", 10000), S("sB", "2026-04-07", 10000)]
    out = reconcile(ledger, statement)
    assert out["matches"] == [
        {"ref": "sB", "entry_ids": ["e1"], "kind": "exact"},
    ]
    assert out["unmatched_statement"] == ["sA"]


def test_duplicate_amounts_pair_deterministically():
    ledger = [L("e2", "2026-05-02", -300), L("e1", "2026-05-02", -300)]
    statement = [S("s1", "2026-05-02", -300)]
    out = reconcile(ledger, statement)
    assert out["matches"] == [
        {"ref": "s1", "entry_ids": ["e1"], "kind": "exact"},
    ]
    assert out["unmatched_ledger"] == ["e2"]


def test_fuzzy_prefers_closest_then_earlier():
    ledger = [
        L("e_far", "2026-06-09", -8000),   # 3 days off
        L("e_near", "2026-06-07", -8000),  # 1 day off
    ]
    out = reconcile(ledger, [S("s1", "2026-06-06", -8000)])
    assert out["matches"][0]["entry_ids"] == ["e_near"]
    assert out["matches"][0]["kind"] == "date"

    ledger = [
        L("e_after", "2026-06-08", -8000),   # 2 days after
        L("e_before", "2026-06-04", -8000),  # 2 days before
    ]
    out = reconcile(ledger, [S("s1", "2026-06-06", -8000)])
    assert out["matches"][0]["entry_ids"] == ["e_before"], "tie -> earlier"


def test_tolerance_is_respected():
    ledger = [L("e1", "2026-07-10", 2500)]
    line = [S("s1", "2026-07-14", 2500)]              # 4 days off
    assert reconcile(ledger, line)["matches"] == []
    out = reconcile(ledger, line, tolerance=5)
    assert out["matches"][0]["kind"] == "date"
    out = reconcile([L("e1", "2026-07-11", 2500)], line, tolerance=0)
    assert out["matches"] == [], "tolerance=0 must disable fuzzy matching"


def test_split_pairs_before_triples():
    ledger = [
        L("g1", "2026-08-03", -4000),
        L("g2", "2026-08-03", -6000),
        L("g3", "2026-08-04", -4000),
        L("g4", "2026-08-04", -6000),
        L("g5", "2026-08-05", -4000),
    ]
    out = reconcile(ledger, [S("s1", "2026-08-04", -12000)])
    (m,) = out["matches"]
    assert m["kind"] == "split"
    assert m["entry_ids"] == ["g2", "g4"], "first pair in (date,id) order"


def test_split_needs_three_sometimes():
    ledger = [
        L("h1", "2026-09-01", -2500),
        L("h2", "2026-09-01", -2500),
        L("h3", "2026-09-02", -5000),
    ]
    out = reconcile(ledger, [S("s1", "2026-09-01", -10000)])
    assert out["matches"] == [
        {"ref": "s1", "entry_ids": ["h1", "h2", "h3"], "kind": "split"},
    ]
    assert out["unmatched_ledger"] == []


def test_split_caps_at_three_and_respects_tolerance():
    quads = [L("i%d" % k, "2026-10-01", -2500) for k in range(1, 5)]
    out = reconcile(quads, [S("s1", "2026-10-01", -10000)])
    assert out["matches"] == [], "a 4-way split should not be attempted"

    ledger = [
        L("j1", "2026-10-01", -7000),
        L("j2", "2026-10-20", -3000),      # way outside tolerance
    ]
    out = reconcile(ledger, [S("s1", "2026-10-02", -10000)])
    assert out["matches"] == []
    assert out["unmatched_ledger"] == ["j1", "j2"]


def test_inputs_not_mutated_and_empty_ok():
    ledger = [L("e1", "2026-03-01", -4500)]
    statement = [S("s1", "2026-03-01", -4500)]
    lfrozen, sfrozen = copy.deepcopy(ledger), copy.deepcopy(statement)
    reconcile(ledger, statement)
    assert ledger == lfrozen and statement == sfrozen
    out = reconcile([], [])
    assert out == {"matches": [], "unmatched_ledger": [],
                   "unmatched_statement": []}


def test_validation():
    good_l = [L("e1", "2026-03-01", -4500)]
    good_s = [S("s1", "2026-03-01", -4500)]
    cases = [
        (good_l + [L("e1", "2026-03-02", 1)], good_s),       # dup id
        (good_l, good_s + [S("s1", "2026-03-02", 1)]),       # dup ref
        ([L("e9", "03/01/2026", 1)], good_s),                # bad date
        ([L("e9", "2026-02-30", 1)], good_s),                # impossible date
        ([L("e9", "2026-03-01", 45.0)], good_s),             # float cents
    ]
    for ledger, statement in cases:
        try:
            reconcile(ledger, statement)
            assert False, "accepted %r / %r" % (ledger, statement)
        except ValueError:
            pass
    for tol in [-1, 2.5, None]:
        try:
            reconcile(good_l, good_s, tolerance=tol)
            assert False, "accepted tolerance %r" % (tol,)
        except ValueError:
            pass


CHECKS = [
    test_march_close_end_to_end,
    test_exact_pass_runs_before_fuzzy_for_every_line,
    test_duplicate_amounts_pair_deterministically,
    test_fuzzy_prefers_closest_then_earlier,
    test_tolerance_is_respected,
    test_split_pairs_before_triples,
    test_split_needs_three_sometimes,
    test_split_caps_at_three_and_respects_tolerance,
    test_inputs_not_mutated_and_empty_ok,
    test_validation,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
