use std::collections::BTreeMap;

use crate::id::{BadgeId, Visitor};

/// Column width for the name field, fixed by the label stock in the
/// badge printer.
pub const NAME_COLS: usize = 18;

/// One printed badge line: right-aligned number, then the name padded
/// (or clipped) to the label stock width.
pub fn badge_line<'a>(id: BadgeId<Visitor>, name: &str) -> String {
    format!("[{:>4}] {:<width$}", id.raw(), clip(name), width = NAME_COLS)
}

/// Header stamped on the first label of each day's roll.
pub fn day_header<'h>(gate: &str, day: &str) -> String {
    format!("== {} — {} ==", gate, day)
}

fn clip(name: &str) -> &str {
    if name.len() > NAME_COLS {
        &name[..NAME_COLS]
    } else {
        name
    }
}
