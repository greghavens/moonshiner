//! Quarterly QA scorecard for the support organisation.
//!
//! Every reviewed ticket gets a 0–100 score and a difficulty class; harder
//! tickets weigh more in an agent's average. Reviews scoring
//! [`EXCELLENT_CUTOFF`] or above also accrue bonus points equal to the
//! review's weight — those feed the quarterly awards report.

/// Difficulty class assigned by the reviewer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Difficulty {
    Routine,
    Standard,
    Complex,
    Escalated,
}

/// How much a review of this difficulty weighs in the average.
pub fn weight_of(difficulty: Difficulty) -> u64 {
    match difficulty {
        Difficulty::Routine => 1,
        Difficulty::Standard => 2,
        Difficulty::Complex => 3,
        Difficulty::Escalated => 5,
    }
}

/// Reviews at or above this score earn bonus points.
pub const EXCELLENT_CUTOFF: u32 = 90;

/// One reviewed ticket.
#[derive(Debug, Clone)]
pub struct Review {
    pub score: u32,
    pub difficulty: Difficulty,
}

/// The quarterly numbers for one agent.
#[derive(Debug, Clone, PartialEq)]
pub struct Summary {
    pub weighted_average: f64,
    pub bonus_points: u64,
    pub weight_total: u64,
    pub review_count: usize,
}

/// Fold one agent's reviews into their scorecard summary.
pub fn summarize(reviews: &[Review]) -> Summary {
    let mut points: u64 = 0;
    let mut weight_total: u64 = 0;
    let bonus_points: u64 = 0;
    for review in reviews {
        let w = weight_of(review.difficulty);
        points += review.score as u64 * w;
        weight_total += w;
        if review.score >= EXCELLENT_CUTOFF {
            let bonus_points = bonus_points + w;
            debug_assert!(
                bonus_points <= weight_total,
                "bonus can never outrun total weight"
            );
        }
    }
    let weighted_average = if weight_total == 0 {
        0.0
    } else {
        (points / weight_total) as f64
    };
    Summary {
        weighted_average,
        bonus_points,
        weight_total,
        review_count: reviews.len(),
    }
}

/// Does this agent's weighted average clear the promotion bar?
pub fn meets_bar(reviews: &[Review], bar: f64) -> bool {
    summarize(reviews).weighted_average >= bar
}
