use rs_weightavg::{meets_bar, summarize, weight_of, Difficulty, Review, Summary};

fn r(score: u32, difficulty: Difficulty) -> Review {
    Review { score, difficulty }
}

use Difficulty::{Complex, Escalated, Routine, Standard};

#[test]
fn weight_table_is_pinned() {
    assert_eq!(weight_of(Routine), 1);
    assert_eq!(weight_of(Standard), 2);
    assert_eq!(weight_of(Complex), 3);
    assert_eq!(weight_of(Escalated), 5);
}

#[test]
fn empty_scorecard_is_all_zeroes() {
    assert_eq!(
        summarize(&[]),
        Summary {
            weighted_average: 0.0,
            bonus_points: 0,
            weight_total: 0,
            review_count: 0,
        }
    );
}

#[test]
fn single_review_average_is_its_score() {
    let s = summarize(&[r(84, Standard)]);
    assert_eq!(s.weighted_average, 84.0);
    assert_eq!(s.weight_total, 2);
    assert_eq!(s.review_count, 1);
}

#[test]
fn identical_scores_average_exactly() {
    let s = summarize(&[r(80, Routine), r(80, Complex), r(80, Escalated)]);
    assert_eq!(s.weighted_average, 80.0);
}

#[test]
fn counts_and_weights_accumulate() {
    let s = summarize(&[r(60, Routine), r(70, Standard), r(80, Complex)]);
    assert_eq!(s.review_count, 3);
    assert_eq!(s.weight_total, 6);
}

#[test]
fn meets_bar_at_a_whole_number_average() {
    let reviews = [r(84, Standard)];
    assert!(meets_bar(&reviews, 84.0));
    assert!(!meets_bar(&reviews, 84.5));
}

#[test]
fn fractional_average_is_not_shaved_down() {
    // 80 and 85, equal weights: the average is 82.5, not 82.
    let s = summarize(&[r(80, Routine), r(85, Routine)]);
    assert_eq!(s.weighted_average, 82.5);
}

#[test]
fn weighted_fraction_survives() {
    // (73*1 + 80*2) / 3 = 233/3
    let s = summarize(&[r(73, Routine), r(80, Standard)]);
    assert_eq!(s.weighted_average, 233.0 / 3.0);
}

#[test]
fn promotion_bar_honours_the_half_point() {
    let reviews = [r(80, Routine), r(85, Routine)];
    assert!(meets_bar(&reviews, 82.5));
}

#[test]
fn no_bonus_below_the_cutoff() {
    let s = summarize(&[r(89, Escalated), r(88, Complex)]);
    assert_eq!(s.bonus_points, 0);
}

#[test]
fn cutoff_is_inclusive_at_ninety() {
    let s = summarize(&[r(90, Routine)]);
    assert_eq!(s.bonus_points, 1);
    assert_eq!(s.weighted_average, 90.0);
}

#[test]
fn bonus_accrues_the_review_weight() {
    // 95 on a Complex (w3) and 92 on a Routine (w1): 4 bonus points.
    let s = summarize(&[r(95, Complex), r(92, Routine), r(70, Standard)]);
    assert_eq!(s.bonus_points, 4);
}

#[test]
fn all_excellent_quarter_keeps_average_and_bonus() {
    let s = summarize(&[r(90, Routine), r(95, Routine), r(100, Routine)]);
    assert_eq!(s.weighted_average, 95.0);
    assert_eq!(s.bonus_points, 3);
}

#[test]
fn full_summary_for_a_mixed_agent() {
    let reviews = [r(95, Complex), r(92, Routine), r(70, Standard)];
    assert_eq!(
        summarize(&reviews),
        Summary {
            weighted_average: 517.0 / 6.0,
            bonus_points: 4,
            weight_total: 6,
            review_count: 3,
        }
    );
}

#[test]
fn quarter_regression_dataset() {
    let reviews = [
        r(95, Escalated),
        r(88, Complex),
        r(92, Standard),
        r(76, Routine),
        r(90, Complex),
        r(84, Standard),
        r(69, Routine),
        r(97, Standard),
        r(81, Escalated),
    ];
    let s = summarize(&reviews);
    assert_eq!(s.weighted_average, 2105.0 / 24.0);
    assert_eq!(s.bonus_points, 12);
    assert_eq!(s.weight_total, 24);
    assert_eq!(s.review_count, 9);
}
