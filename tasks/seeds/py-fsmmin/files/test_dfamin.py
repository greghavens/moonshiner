"""Acceptance tests for dfamin.py — offline DFA algebra for the validator
table pipeline: validate / accepts / trim / minimize / equivalent.

All machines here are complete DFAs over small single-character alphabets,
declared as plain dicts. Expected minimize() outputs were worked out by hand
with partition refinement and BFS renumbering; do not "fix" them.

Run: python3 test_dfamin.py
"""

import copy

import dfamin


# ------------------------------------------------------------ fixture DFAs


def contains_ab():
    """Minimal 3-state DFA: strings over {a,b} containing 'ab'."""
    return {
        "states": ["q0", "q1", "q2"],
        "alphabet": ["a", "b"],
        "delta": {
            "q0": {"a": "q1", "b": "q0"},
            "q1": {"a": "q1", "b": "q2"},
            "q2": {"a": "q2", "b": "q2"},
        },
        "initial": "q0",
        "accepting": ["q2"],
    }


def contains_ab_bloated():
    """Same language, 5 states: s4 duplicates s1, s3 duplicates s2."""
    return {
        "states": ["s0", "s1", "s2", "s3", "s4"],
        "alphabet": ["a", "b"],
        "delta": {
            "s0": {"a": "s1", "b": "s0"},
            "s1": {"a": "s4", "b": "s2"},
            "s2": {"a": "s2", "b": "s2"},
            "s3": {"a": "s3", "b": "s3"},
            "s4": {"a": "s4", "b": "s3"},
        },
        "initial": "s0",
        "accepting": ["s2", "s3"],
    }


def contains_ba():
    """Minimal 3-state DFA: strings over {a,b} containing 'ba'.
    Alphabet listed in a different order on purpose."""
    return {
        "states": ["r0", "r1", "r2"],
        "alphabet": ["b", "a"],
        "delta": {
            "r0": {"a": "r0", "b": "r1"},
            "r1": {"a": "r2", "b": "r1"},
            "r2": {"a": "r2", "b": "r2"},
        },
        "initial": "r0",
        "accepting": ["r2"],
    }


def parity(accept_even):
    """1-symbol alphabet; accepts words with an even (or odd) count of 'a'."""
    return {
        "states": ["e0", "e1"],
        "alphabet": ["a"],
        "delta": {"e0": {"a": "e1"}, "e1": {"a": "e0"}},
        "initial": "e0",
        "accepting": ["e0"] if accept_even else ["e1"],
    }


CANONICAL_CONTAINS_AB = {
    "states": [0, 1, 2],
    "alphabet": ["a", "b"],
    "delta": {
        0: {"a": 1, "b": 0},
        1: {"a": 1, "b": 2},
        2: {"a": 2, "b": 2},
    },
    "initial": 0,
    "accepting": [2],
}


# ------------------------------------------------------------------ helpers


def expect_dfa_error(fn, *args):
    try:
        fn(*args)
    except dfamin.DFAError:
        return
    raise AssertionError("DFAError expected from %s%r" % (fn.__name__, args))


# --------------------------------------------------------------------- tests


def test_validate_rejects_malformed_machines():
    good = contains_ab()
    dfamin.validate(good)  # sanity: fixture is well-formed

    incomplete = contains_ab()
    del incomplete["delta"]["q1"]["b"]  # missing (state, symbol) row
    expect_dfa_error(dfamin.validate, incomplete)

    stray_target = contains_ab()
    stray_target["delta"]["q0"]["a"] = "q9"
    expect_dfa_error(dfamin.validate, stray_target)

    bad_initial = contains_ab()
    bad_initial["initial"] = "nowhere"
    expect_dfa_error(dfamin.validate, bad_initial)

    bad_accepting = contains_ab()
    bad_accepting["accepting"] = ["q2", "q7"]
    expect_dfa_error(dfamin.validate, bad_accepting)

    fat_symbol = contains_ab()
    fat_symbol["alphabet"] = ["a", "bb"]
    expect_dfa_error(dfamin.validate, fat_symbol)

    dup_states = contains_ab()
    dup_states["states"] = ["q0", "q1", "q2", "q1"]
    expect_dfa_error(dfamin.validate, dup_states)

    extra_key = contains_ab()
    extra_key["comment"] = "vendor 12"
    expect_dfa_error(dfamin.validate, extra_key)

    # every public entry point validates its input
    expect_dfa_error(dfamin.minimize, incomplete)
    expect_dfa_error(dfamin.trim, incomplete)
    expect_dfa_error(dfamin.accepts, incomplete, "ab")


def test_accepts_runs_words():
    m = contains_ab()
    assert dfamin.accepts(m, "ab") is True
    assert dfamin.accepts(m, "aab") is True
    assert dfamin.accepts(m, "ba") is False
    assert dfamin.accepts(m, "") is False
    assert dfamin.accepts(parity(True), "") is True, "empty word, accepting initial"
    expect_dfa_error(dfamin.accepts, m, "axb")  # 'x' is not in the alphabet


def test_trim_drops_unreachable_states():
    m = contains_ab_bloated()
    m["states"] += ["u1", "u2"]
    m["delta"]["u1"] = {"a": "u1", "b": "u1"}
    m["delta"]["u2"] = {"a": "s0", "b": "u1"}
    m["accepting"] = ["s2", "s3", "u1"]

    t = dfamin.trim(m)
    assert t["states"] == ["s0", "s1", "s2", "s3", "s4"], \
        "reachable states keep their original order and names"
    assert sorted(t["delta"].keys()) == ["s0", "s1", "s2", "s3", "s4"]
    assert t["accepting"] == ["s2", "s3"]
    assert t["initial"] == "s0"
    assert t["alphabet"] == ["a", "b"]

    # trimming a fully reachable machine changes nothing
    whole = dfamin.trim(contains_ab())
    assert whole == contains_ab()


def test_minimize_collapses_duplicate_states():
    got = dfamin.minimize(contains_ab_bloated())
    assert got == CANONICAL_CONTAINS_AB, "hand-computed canonical form"


def test_minimize_is_canonical_across_isomorphic_machines():
    # Same machine as contains_ab_bloated, states renamed and reordered:
    # s0->mm  s1->zz  s2->qq  s3->pp  s4->kk
    renamed = {
        "states": ["qq", "kk", "mm", "zz", "pp"],
        "alphabet": ["a", "b"],
        "delta": {
            "mm": {"a": "zz", "b": "mm"},
            "zz": {"a": "kk", "b": "qq"},
            "qq": {"a": "qq", "b": "qq"},
            "pp": {"a": "pp", "b": "pp"},
            "kk": {"a": "kk", "b": "pp"},
        },
        "initial": "mm",
        "accepting": ["pp", "qq"],
    }
    assert dfamin.minimize(renamed) == dfamin.minimize(contains_ab_bloated()), \
        "isomorphic inputs must produce the identical canonical structure"
    assert dfamin.minimize(renamed) == CANONICAL_CONTAINS_AB


def test_minimize_empty_language_collapses_to_one_state():
    m = {
        "states": ["t0", "t1"],
        "alphabet": ["a", "b"],
        "delta": {"t0": {"a": "t1", "b": "t0"}, "t1": {"a": "t0", "b": "t1"}},
        "initial": "t0",
        "accepting": [],
    }
    assert dfamin.minimize(m) == {
        "states": [0],
        "alphabet": ["a", "b"],
        "delta": {0: {"a": 0, "b": 0}},
        "initial": 0,
        "accepting": [],
    }


def test_minimize_all_accepting_collapses_to_one_state():
    m = {
        "states": ["t0", "t1"],
        "alphabet": ["a", "b"],
        "delta": {"t0": {"a": "t1", "b": "t0"}, "t1": {"a": "t0", "b": "t1"}},
        "initial": "t0",
        "accepting": ["t0", "t1"],
    }
    assert dfamin.minimize(m) == {
        "states": [0],
        "alphabet": ["a", "b"],
        "delta": {0: {"a": 0, "b": 0}},
        "initial": 0,
        "accepting": [0],
    }


def test_minimize_already_minimal_and_idempotent():
    even = dfamin.minimize(parity(True))
    assert even == {
        "states": [0, 1],
        "alphabet": ["a"],
        "delta": {0: {"a": 1}, 1: {"a": 0}},
        "initial": 0,
        "accepting": [0],
    }
    once = dfamin.minimize(contains_ab_bloated())
    assert dfamin.minimize(once) == once, "minimize is idempotent"


def test_minimize_prunes_unreachable_before_refining():
    m = contains_ab_bloated()
    m["states"] += ["u1"]
    m["delta"]["u1"] = {"a": "u1", "b": "u1"}
    m["accepting"] = ["s2", "s3", "u1"]
    assert dfamin.minimize(m) == CANONICAL_CONTAINS_AB, \
        "unreachable states must not appear in the output"


def test_equivalent_machines_report_true():
    ok, word = dfamin.equivalent(contains_ab_bloated(), contains_ab())
    assert ok is True and word is None


def test_inequivalent_machines_give_shortest_counterexample():
    a, b = contains_ab(), contains_ba()
    ok, word = dfamin.equivalent(a, b)
    assert ok is False
    assert word == "ab", "BFS with sorted symbols pins this exact witness"
    assert dfamin.accepts(a, word) != dfamin.accepts(b, word), \
        "the witness must actually distinguish the machines"


def test_counterexample_can_be_the_empty_word():
    ok, word = dfamin.equivalent(parity(True), parity(False))
    assert ok is False
    assert word == ""


def test_equivalence_against_own_minimized_form():
    m = contains_ab_bloated()
    ok, word = dfamin.equivalent(m, dfamin.minimize(m))
    assert ok is True and word is None, \
        "string-named and integer-named states must mix fine in the product"


def test_alphabets_must_match_as_sets():
    other = {
        "states": ["z"],
        "alphabet": ["a", "c"],
        "delta": {"z": {"a": "z", "c": "z"}},
        "initial": "z",
        "accepting": [],
    }
    expect_dfa_error(dfamin.equivalent, contains_ab(), other)
    # ...but listing order does not matter: contains_ba declares ["b", "a"]
    ok, _ = dfamin.equivalent(contains_ba(), contains_ba())
    assert ok is True


def test_inputs_are_never_mutated():
    m = contains_ab_bloated()
    m["states"] += ["u1"]
    m["delta"]["u1"] = {"a": "u1", "b": "u1"}
    snapshot = copy.deepcopy(m)
    dfamin.trim(m)
    dfamin.minimize(m)
    dfamin.accepts(m, "ab")
    dfamin.equivalent(m, contains_ab())
    assert m == snapshot, "public functions must not modify their arguments"


def main():
    tests = [
        test_validate_rejects_malformed_machines,
        test_accepts_runs_words,
        test_trim_drops_unreachable_states,
        test_minimize_collapses_duplicate_states,
        test_minimize_is_canonical_across_isomorphic_machines,
        test_minimize_empty_language_collapses_to_one_state,
        test_minimize_all_accepting_collapses_to_one_state,
        test_minimize_already_minimal_and_idempotent,
        test_minimize_prunes_unreachable_before_refining,
        test_equivalent_machines_report_true,
        test_inequivalent_machines_give_shortest_counterexample,
        test_counterexample_can_be_the_empty_word,
        test_equivalence_against_own_minimized_form,
        test_alphabets_must_match_as_sets,
        test_inputs_are_never_mutated,
    ]
    for t in tests:
        t()
    print("all %d test groups passed" % len(tests))


if __name__ == "__main__":
    main()
