use std::error::Error;
use std::fmt;

/// House default: how long a route stays locked after the train clears,
/// when the timetable column is blank or unreadable.
pub const DEFAULT_DWELL_SECS: u32 = 45;

/// A route request the interlocking refused.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlanError {
    pub lever: String,
    pub reason: String,
}

impl fmt::Display for PlanError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "lever {}: {}", self.lever, self.reason)
    }
}

impl Error for PlanError {}

/// Seconds a route stays locked, read from the timetable column.
pub fn dwell_secs(entry: &str) -> u32 {
    let cleaned = entry.trim() else {
        return DEFAULT_DWELL_SECS;
    };
    cleaned.parse().unwrap_or(DEFAULT_DWELL_SECS)
}

/// Lever labels come off the frame scanner padded on both sides and in
/// whatever case the plate was stamped in.
pub fn lever_label(raw: &str) -> String {
    raw.trim_left().trim_right().to_uppercase()
}

/// Whether a locked lever may be released.
pub fn check_release(lever: &str, points_locked: bool) -> Result<(), PlanError> {
    if points_locked {
        return Err(PlanError {
            lever: lever.to_string(),
            reason: "points locked out".to_string(),
        });
    }
    Ok(())
}

/// One line for the box journal when a request is refused.
pub fn journal_line(err: &PlanError) -> String {
    format!("REFUSED {}", err.description())
}
