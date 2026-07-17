"""Acceptance checks for strength.py. Run: python3 test_strength.py"""
import strength
from strength import char_classes, score, verdict


# ---------------------------------------------------------------- existing

def test_empty_password_scores_zero():
    assert score("") == 0
    assert verdict("") == "very weak"


def test_char_classes_are_detected():
    assert char_classes("abc") == {"lower"}
    assert char_classes("aB3!") == {"lower", "upper", "digit", "symbol"}
    assert "symbol" in char_classes("päss"), "non-ASCII must count as symbol"


def test_length_tiers():
    assert score("a" * 7) == 0
    assert score("a" * 8) == 1
    assert score("a" * 12) == 2
    assert score("a" * 16) == 3


def test_variety_adds_points():
    assert score("abcd1234") == 2          # 8 chars, two classes
    assert score("aB3!aB3!") == 4          # 8 chars, four classes


def test_score_caps_at_four():
    assert score("aB3!" * 4) == 4
    assert verdict("aB3!" * 4) == "strong"


def test_verdict_labels_follow_score():
    assert verdict("a" * 8) == "weak"
    assert verdict("abcd1234") == "fair"
    assert verdict("abcdef123456") == "good"


# ----------------------------------------------------------------- feature

def test_entropy_bits_from_length_and_pool():
    assert strength.entropy_bits("") == 0.0
    assert strength.entropy_bits("kudzmvpq") == 37.6         # 8 * log2(26)
    assert strength.entropy_bits("Tr0ub4dor&3") == 72.1      # 11 * log2(94)


def test_entropy_pool_counts_each_class_once():
    assert strength.entropy_bits("zzzz") == 18.8             # still pool 26
    assert strength.entropy_bits("¡¡¡¡") == 20.0  # symbols: 4 * log2(32)


def test_sequences_need_four_consecutive_characters():
    assert strength.find_patterns("xabcdz") == ["sequence"]
    assert strength.find_patterns("9876") == ["sequence"]
    assert strength.find_patterns("AbCd") == ["sequence"]
    assert strength.find_patterns("abc123") == []


def test_repeats_and_keyboard_runs():
    assert strength.find_patterns("xj!aaa") == ["repeat"]
    assert strength.find_patterns("Asdfgh!") == ["keyboard"]
    assert strength.find_patterns("9poiuy") == ["keyboard"], "reversed rows count"


def test_year_only_penalized_as_suffix():
    assert strength.find_patterns("summer2024") == ["year"]
    assert strength.find_patterns("2024summer") == []
    assert strength.find_patterns("summer1899") == []


def test_patterns_report_in_canonical_order():
    assert strength.find_patterns("abcdqwerty") == ["sequence", "keyboard"]
    assert strength.find_patterns("aaaqwerty2024") == ["repeat", "keyboard", "year"]


def test_effective_bits_subtract_ten_per_pattern():
    assert strength.effective_bits("qwerty1234") == 31.7     # 51.7 - 2 patterns
    assert strength.effective_bits("abcd") == 8.8            # 18.8 - 1 pattern
    assert strength.effective_bits("111234") == 0.0, "never below zero"


def test_entropy_score_tiers():
    assert strength.entropy_score("") == 0
    assert strength.entropy_score("abcd") == 0               # 8.8 bits
    assert strength.entropy_score("qwerty1234") == 1         # 31.7 bits
    assert strength.entropy_score("troubadour") == 2         # 47.0 bits
    assert strength.entropy_score("Tr0ub4dor&3") == 3        # 72.1 bits
    assert strength.entropy_score("correct horse battery staple") == 4


def test_suggestions_cover_gaps_and_patterns_in_order():
    assert strength.suggest("qwerty1999") == [
        "use at least 12 characters",
        "add uppercase letters",
        "add symbols",
        "avoid repeating the same character",
        "avoid keyboard runs like 'qwerty'",
        "don't end the password with a year",
    ], strength.suggest("qwerty1999")


def test_empty_password_gets_the_basics():
    assert strength.suggest("") == [
        "use at least 12 characters",
        "add uppercase letters",
        "add lowercase letters",
        "add digits",
        "add symbols",
    ], strength.suggest("")


def test_no_suggestions_when_nothing_to_fix():
    assert strength.suggest("M9!xTk#2pQ%7vR@4") == []


def test_evaluate_bundles_the_whole_report():
    report = strength.evaluate("Password2024")
    assert report == {
        "bits": 61.5,
        "score": 3,
        "verdict": "good",
        "patterns": ["year"],
        "suggestions": ["add symbols", "don't end the password with a year"],
    }, report


def test_legacy_scoring_is_untouched():
    assert score("abcd1234") == 2
    assert verdict("a" * 8) == "weak"


EXISTING = [
    test_empty_password_scores_zero,
    test_char_classes_are_detected,
    test_length_tiers,
    test_variety_adds_points,
    test_score_caps_at_four,
    test_verdict_labels_follow_score,
]

FEATURE = [
    test_entropy_bits_from_length_and_pool,
    test_entropy_pool_counts_each_class_once,
    test_sequences_need_four_consecutive_characters,
    test_repeats_and_keyboard_runs,
    test_year_only_penalized_as_suffix,
    test_patterns_report_in_canonical_order,
    test_effective_bits_subtract_ten_per_pattern,
    test_entropy_score_tiers,
    test_suggestions_cover_gaps_and_patterns_in_order,
    test_empty_password_gets_the_basics,
    test_no_suggestions_when_nothing_to_fix,
    test_evaluate_bundles_the_whole_report,
    test_legacy_scoring_is_untouched,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
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
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
