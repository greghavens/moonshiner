"""Tests for the retention policy loader and the new policy linter.

The EXISTING BEHAVIOR block passes against the shipped retention.py and must
keep passing. The LINTING blocks below cover the new lint_policy() API and
fail until it is implemented.

Run: python3 test_retention.py
"""
from retention import evaluate, load_policy

BASIC = """\
# snapshot retention for the metrics cluster
rule hot-db    match=db-*      max_age=48h   action=keep
rule warm-db   match=db-*      max_age=30d   action=archive   # then archive
rule scratch   match=tmp-nightly  max_age=6h  action=delete

default delete
"""


# ---------------------------------------------------------------- existing behavior

def test_load_policy_basics():
    policy = load_policy(BASIC)
    assert [r.name for r in policy.rules] == ["hot-db", "warm-db", "scratch"]
    assert [r.pattern for r in policy.rules] == ["db-*", "db-*", "tmp-nightly"]
    assert [r.max_age_hours for r in policy.rules] == [48, 720, 6]
    assert [r.action for r in policy.rules] == ["keep", "archive", "delete"]
    assert policy.default_action == "delete"
    # no default line -> keep
    assert load_policy("rule a match=x max_age=1h action=keep").default_action == "keep"
    assert load_policy("").rules == []


def test_evaluate_first_match_wins_and_age_bounds():
    policy = load_policy(BASIC)
    assert evaluate(policy, "db-orders", 10) == "keep"
    assert evaluate(policy, "db-orders", 48) == "archive"     # 48h tier is exclusive
    assert evaluate(policy, "db-orders", 700) == "archive"
    assert evaluate(policy, "db-orders", 720) == "delete"     # falls to default
    assert evaluate(policy, "tmp-nightly", 2) == "delete"
    assert evaluate(policy, "tmp-nightly-old", 2) == "delete"  # literal, no prefixing
    assert evaluate(policy, "web-assets", 1) == "delete"       # default


def test_load_policy_rejects_bad_input():
    bad = [
        "rule a match=x max_age=1h action=keep\nrule a match=y max_age=2h action=keep",
        "rule a match=x max_age=1h action=shred",
        "rule a match=x max_age=0h action=keep",
        "rule a match=x max_age=12x action=keep",
        "rule a match=x max_age=h action=keep",
        "rule a match=x max_age=-3h action=keep",
        "rule a match=db-*-prod max_age=1h action=keep",
        "rule a match=db-** max_age=1h action=keep",
        "rule a match=x max_age=1h action=keep color=red",
        "rule a match=x action=keep",
        "rule a match=x match=y max_age=1h action=keep",
        "default keep\ndefault delete",
        "default sideways",
        "retain everything forever",
    ]
    for text in bad:
        try:
            load_policy(text)
        except ValueError:
            continue
        raise AssertionError(f"load_policy should reject: {text!r}")


# ---------------------------------------------------------------- policy linting

def test_clean_policies_have_no_findings():
    from retention import lint_policy
    assert lint_policy(load_policy(BASIC)) == []
    # narrower rule first, broader after: reachable, not shadowed
    assert lint_policy(load_policy(
        "rule prod match=db-prod-* max_age=12h action=keep\n"
        "rule any  match=db-*      max_age=12h action=delete\n")) == []
    # broader rule first but with a SMALLER max_age: later tier still fires
    assert lint_policy(load_policy(
        "rule short match=db-*      max_age=24h action=keep\n"
        "rule long  match=db-prod-* max_age=48h action=archive\n")) == []


def test_duplicate_rule_finding():
    from retention import lint_policy
    policy = load_policy(
        "rule keep-hot   match=db-* max_age=48h action=keep\n"
        "rule keep-again match=db-* max_age=2d  action=keep\n")
    assert lint_policy(policy) == [{
        "code": "duplicate-rule",
        "rule": "keep-again",
        "other": "keep-hot",
        "message": "rule 'keep-again' repeats rule 'keep-hot'",
        "suggestion": "delete rule 'keep-again'",
    }]


def test_conflicting_action_finding():
    from retention import lint_policy
    policy = load_policy(
        "rule hot   match=db-* max_age=48h action=keep\n"
        "rule purge match=db-* max_age=48h action=delete\n")
    assert lint_policy(policy) == [{
        "code": "conflicting-action",
        "rule": "purge",
        "other": "hot",
        "message": ("rule 'purge' contradicts rule 'hot': "
                    "same scope, action 'delete' vs 'keep'"),
        "suggestion": "keep one action for pattern 'db-*' at 48h",
    }]


def test_unreachable_tier_finding():
    from retention import lint_policy
    policy = load_policy(
        "rule monthly match=db-* max_age=30d action=archive\n"
        "rule weekly  match=db-* max_age=7d  action=keep\n")
    assert lint_policy(policy) == [{
        "code": "unreachable-tier",
        "rule": "weekly",
        "other": "monthly",
        "message": ("rule 'weekly' never fires: rule 'monthly' already "
                    "covers pattern 'db-*' up to 720h"),
        "suggestion": "order 'db-*' tiers by increasing max_age",
    }]


def test_shadowed_rule_finding():
    from retention import lint_policy
    policy = load_policy(
        "rule any-db match=db-*      max_age=48h action=archive\n"
        "rule prod   match=db-prod-* max_age=24h action=keep\n")
    assert lint_policy(policy) == [{
        "code": "shadowed-rule",
        "rule": "prod",
        "other": "any-db",
        "message": ("rule 'prod' never fires: rule 'any-db' matches "
                    "everything it matches first"),
        "suggestion": "move rule 'prod' above rule 'any-db'",
    }]
    # a catch-all first shadows a literal too
    policy = load_policy(
        "rule everything match=*        max_age=90d action=keep\n"
        "rule one-share  match=nas-media max_age=30d action=delete\n")
    got = lint_policy(policy)
    assert len(got) == 1 and got[0]["code"] == "shadowed-rule"
    assert got[0]["rule"] == "one-share" and got[0]["other"] == "everything"


def test_one_finding_per_rule_against_earliest_cause():
    from retention import lint_policy
    policy = load_policy(
        "rule catch-all match=*    max_age=100d action=keep\n"
        "rule db-a      match=db-* max_age=10d  action=keep\n"
        "rule db-b      match=db-* max_age=10d  action=keep\n")
    got = lint_policy(policy)
    # db-b duplicates db-a, but the catch-all kills it first -> one finding,
    # coded against the earliest responsible rule
    assert [f["rule"] for f in got] == ["db-a", "db-b"]
    assert [f["code"] for f in got] == ["shadowed-rule", "shadowed-rule"]
    assert [f["other"] for f in got] == ["catch-all", "catch-all"]


def test_findings_follow_rule_order():
    from retention import lint_policy
    policy = load_policy(
        "rule tier-a match=logs-* max_age=14d action=archive\n"
        "rule tier-b match=logs-* max_age=7d  action=delete\n"
        "rule fine   match=cache- max_age=1h  action=delete\n"
        "rule tier-c match=logs-* max_age=14d action=archive\n")
    got = lint_policy(policy)
    assert [f["rule"] for f in got] == ["tier-b", "tier-c"]
    assert [f["code"] for f in got] == ["unreachable-tier", "duplicate-rule"]
    assert got[1]["other"] == "tier-a"


def main():
    tests = [
        test_load_policy_basics,
        test_evaluate_first_match_wins_and_age_bounds,
        test_load_policy_rejects_bad_input,
        test_clean_policies_have_no_findings,
        test_duplicate_rule_finding,
        test_conflicting_action_finding,
        test_unreachable_tier_finding,
        test_shadowed_rule_finding,
        test_one_finding_per_rule_against_earliest_cause,
        test_findings_follow_rule_order,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
