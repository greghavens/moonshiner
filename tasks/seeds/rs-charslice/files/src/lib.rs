//! Text helpers for the release-notes generator.
//!
//! Changelog fields get a display budget measured in characters. A value
//! within its budget passes through untouched; anything longer is clipped to
//! budget-minus-one characters with a single `…` appended, so the reader can
//! always tell content was dropped and a clipped value still fits the budget
//! exactly. Commit subjects follow the usual rule: first line of the message,
//! trailing whitespace dropped, budget 72.

/// Display budget for a commit subject line.
pub const SUBJECT_BUDGET: usize = 72;

/// Bound `s` to `max_chars` characters, marking any cut with `…`.
pub fn clip(s: &str, max_chars: usize) -> String {
    if max_chars == 0 {
        return String::new();
    }
    if s.len() <= max_chars {
        return s.to_string();
    }
    format!("{}…", &s[..max_chars - 1])
}

/// The changelog subject for a commit message: first line, trailing
/// whitespace dropped, clipped to [`SUBJECT_BUDGET`].
pub fn first_line_summary(message: &str) -> String {
    let line = message.lines().next().unwrap_or("");
    clip(line.trim_end(), SUBJECT_BUDGET)
}
