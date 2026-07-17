"""Behavior checks for the skills self-assessment scorer.

Run: python3 test_assessment.py
"""
from assessment import completion_rate, summary, total_score, unanswered

BANK = [
    {"id": "git", "kind": "scale", "max": 5, "weight": 2, "assumed": 2},
    {"id": "sql", "kind": "scale", "max": 5, "weight": 1},
    {"id": "oncall", "kind": "choice", "weight": 3, "answer": "page the secondary"},
    {"id": "feedback", "kind": "text"},
]


def test_scoring_basics():
    responses = {
        "git": 4,
        "sql": 3,
        "oncall": "page the secondary",
        "feedback": "great week",
    }
    assert total_score(BANK, responses) == 4 * 2 + 3 + 3, total_score(BANK, responses)

    wrong_choice = dict(responses, oncall="restart the pod")
    assert total_score(BANK, wrong_choice) == 4 * 2 + 3, total_score(BANK, wrong_choice)


def test_lowest_rating_scores_below_a_one():
    hire_zero = {"git": 0, "sql": 3, "oncall": "restart the pod", "feedback": ""}
    hire_one = {"git": 1, "sql": 3, "oncall": "restart the pod", "feedback": ""}
    zero_total = total_score(BANK, hire_zero)
    one_total = total_score(BANK, hire_one)
    assert zero_total == 3, f"a 0 rating is worth 0 points, got total {zero_total}"
    assert zero_total < one_total, (
        f"rating yourself 0 ({zero_total}) must not outscore rating 1 ({one_total})"
    )


def test_everything_submitted_counts_as_complete():
    responses = {"git": 0, "sql": 2, "oncall": "page the secondary", "feedback": ""}
    assert unanswered(BANK, responses) == [], unanswered(BANK, responses)
    assert completion_rate(BANK, responses) == 1.0, completion_rate(BANK, responses)


def test_skips_use_the_assumed_rating():
    responses = {"sql": 2, "oncall": "page the secondary", "feedback": "ok"}
    # git skipped -> assumed rating 2 at weight 2; sql has no assumed rating
    assert total_score(BANK, responses) == 2 * 2 + 2 + 3, total_score(BANK, responses)
    assert unanswered(BANK, responses) == ["git"], unanswered(BANK, responses)

    explicit_none = dict(responses, git=None)
    assert unanswered(BANK, explicit_none) == ["git"], unanswered(BANK, explicit_none)
    assert completion_rate(BANK, explicit_none) == 0.75, completion_rate(BANK, explicit_none)


def test_dashboard_summary_row():
    responses = {"git": 0, "sql": 0, "oncall": "page the secondary", "feedback": ""}
    row = summary(BANK, responses)
    assert row == {"score": 3, "completion": 1.0, "needs_follow_up": []}, row


def main():
    test_scoring_basics()
    test_lowest_rating_scores_below_a_one()
    test_everything_submitted_counts_as_complete()
    test_skips_use_the_assumed_rating()
    test_dashboard_summary_row()
    print("all checks passed")


if __name__ == "__main__":
    main()
