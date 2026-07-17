//! Downtime alerts against the plant threshold.

use crate::model::Report;
use crate::plugin::SummaryPlugin;

/// Any machine at or over this many downtime minutes gets flagged.
pub const DOWNTIME_ALERT_MIN: u64 = 45;

pub struct AlertsSummary;

impl SummaryPlugin for AlertsSummary {
    fn name(&self) -> &'static str {
        "alerts"
    }

    fn lines(&self, report: &Report) -> Vec<String> {
        let mut lines: Vec<String> = report
            .machines
            .iter()
            .filter(|(_, totals)| totals.downtime_min >= DOWNTIME_ALERT_MIN)
            .map(|(machine, totals)| {
                format!("alert: {} downtime {} min", machine, totals.downtime_min)
            })
            .collect();
        if lines.is_empty() {
            lines.push("alerts: none".to_string());
        }
        lines
    }
}
