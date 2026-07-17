//! Plugin traits the pipeline drives.

use crate::model::Report;

/// A writer renders one output artifact for the run.
pub trait WriterPlugin {
    /// Stable name used in `--writers` selection.
    fn name(&self) -> &'static str;
    /// Output filename; may depend on the report (e.g. the shift label).
    fn filename(&self, report: &Report) -> String;
    /// The full file content this writer produces for the report.
    fn render(&self, report: &Report) -> String;
}

/// A summary plugin contributes lines to the run summary, in registry order.
pub trait SummaryPlugin {
    /// Stable name used in `--summaries` selection.
    fn name(&self) -> &'static str;
    fn lines(&self, report: &Report) -> Vec<String>;
}
