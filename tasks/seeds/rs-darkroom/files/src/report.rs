//! The weekly contact-sheet report the lab pins above the sink.

use crate::chem::{dilution, Batch};
use crate::entry::Entry;

/// One line per souped roll, then the batch footer, exactly as the
/// printed report reads.
pub fn contact_sheet(entries: &[Entry], batch: &Batch) -> String {
    let mut lines = vec![format!("CONTACT SHEETS — {} rolls", entries.len())];
    for e in entries {
        lines.push(format!(
            "{} ({}) — {:?} {}min, {} frames",
            e.stock.name,
            e.stock.iso,
            e.developer,
            e.minutes,
            e.sheet_frames()
        ));
    }
    lines.push(format!(
        "batch: {:?} {} ({}ml)",
        batch.developer,
        dilution(batch),
        batch.stock_ml + batch.water_ml
    ));
    lines.join("\n")
}
