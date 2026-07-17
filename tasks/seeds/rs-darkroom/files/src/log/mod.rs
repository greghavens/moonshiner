//! The development log: one entry per roll souped.

mod entry;

pub use crate::chem::FilmStock;

/// Total developing minutes across a session's rolls.
pub fn session_minutes(entries: &[entry::Entry]) -> u32 {
    entries.iter().map(|e| e.minutes).sum()
}
