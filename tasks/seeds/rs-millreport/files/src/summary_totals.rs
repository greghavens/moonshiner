//! Whole-shift totals for the run summary.

use crate::model::Report;
use crate::plugin::SummaryPlugin;

pub struct TotalsSummary;

impl SummaryPlugin for TotalsSummary {
    fn name(&self) -> &'static str {
        "totals"
    }

    fn lines(&self, report: &Report) -> Vec<String> {
        let totals = report.totals();
        vec![
            format!("machines: {}", report.machines.len()),
            format!("produced total: {}", totals.produced),
            format!("downtime total: {} min", totals.downtime_min),
            format!("scrap total: {}", totals.scrap),
        ]
    }
}
