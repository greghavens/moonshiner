//! Shift report: material totals stated in tonnes to two decimals.
//!
//! Site billing rule: a shift total is stated to the nearest 0.01 t
//! (i.e. the nearest 10 kg), with halves rounding up. The report keeps
//! totals as integer hundredths of a tonne so rendering is exact.

use crate::window::ShiftSlice;

/// One report line: a shift, a material, and its rounded total.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReportLine {
    pub start_minute: u32,
    pub material: String,
    /// Hundredths of a tonne: `12.34 t` is stored as `1234`.
    pub centi_tonnes: i64,
}

/// Reduce finished shift slices to report lines, one per shift+material.
pub fn totals(slices: &[ShiftSlice]) -> Vec<ReportLine> {
    let mut out = Vec::new();
    for slice in slices {
        for (material, kg) in &slice.totals {
            out.push(ReportLine {
                start_minute: slice.start_minute,
                material: material.clone(),
                centi_tonnes: (kg / 10.0).ceil() as i64,
            });
        }
    }
    out
}

/// Render `HH:MM material T.TTt` lines, one per report line.
pub fn render(lines: &[ReportLine]) -> String {
    let mut out = String::new();
    for l in lines {
        out.push_str(&format!(
            "{:02}:{:02} {} {}.{:02}t\n",
            l.start_minute / 60,
            l.start_minute % 60,
            l.material,
            l.centi_tonnes / 100,
            l.centi_tonnes % 100
        ));
    }
    out
}
