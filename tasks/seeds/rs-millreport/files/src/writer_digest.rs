//! Human-readable shift digest posted to the ops channel.

use crate::format;
use crate::model::Report;
use crate::plugin::WriterPlugin;

pub struct ShiftDigest;

impl WriterPlugin for ShiftDigest {
    fn name(&self) -> &'static str {
        "digest"
    }

    fn filename(&self, report: &Report) -> String {
        format!("shift_digest_{}.txt", report.shift)
    }

    fn render(&self, report: &Report) -> String {
        let mut out = format!("SHIFT REPORT: {}\n", report.shift);
        out.push_str(&format::table_header());
        out.push('\n');
        for (machine, totals) in &report.machines {
            out.push_str(&format::table_row(
                machine,
                totals.produced,
                totals.downtime_min,
                totals.scrap,
            ));
            out.push('\n');
        }
        out.push_str(&format!("machines: {}\n", report.machines.len()));
        out
    }
}
