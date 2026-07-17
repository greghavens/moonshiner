//! Month-end expense rollup by cost center.
//!
//! Ledger entries are tallied per cost-center code (amounts in cents so the
//! arithmetic is exact); `render` produces the fixed-format text report the
//! reconciliation tooling diffs against the previous night's run. Rows are
//! one per cost center followed by a TOTAL line.

use std::collections::HashMap;

/// Cents formatted as a decimal amount, e.g. `-1042.07`.
pub fn fmt_cents(cents: i64) -> String {
    let sign = if cents < 0 { "-" } else { "" };
    let abs = cents.abs();
    format!("{sign}{}.{:02}", abs / 100, abs % 100)
}

/// Running tally of ledger entries for one accounting month.
#[derive(Debug, Default)]
pub struct Rollup {
    totals: HashMap<String, i64>,
}

impl Rollup {
    pub fn new() -> Self {
        Self::default()
    }

    /// Post one ledger entry (negative amounts are refunds).
    pub fn add(&mut self, center: &str, cents: i64) {
        *self.totals.entry(center.to_string()).or_insert(0) += cents;
    }

    /// Total posted for one cost center, if it has any entries.
    pub fn center_total(&self, center: &str) -> Option<i64> {
        self.totals.get(center).copied()
    }

    /// Grand total across every cost center.
    pub fn total_cents(&self) -> i64 {
        self.totals.values().sum()
    }

    /// Report rows: one `(cost-center, total-cents)` pair per center, in
    /// cost-center code order.
    pub fn rows(&self) -> Vec<(String, i64)> {
        self.totals
            .iter()
            .map(|(center, total)| (center.clone(), *total))
            .collect()
    }

    /// The fixed-format month-end report: one `CODE  AMOUNT` line per cost
    /// center in code order, then a `TOTAL` line.
    pub fn render(&self) -> String {
        let mut out = String::new();
        for (center, total) in self.rows() {
            out.push_str(&format!("{center}  {}\n", fmt_cents(total)));
        }
        out.push_str(&format!("TOTAL  {}\n", fmt_cents(self.total_cents())));
        out
    }
}
