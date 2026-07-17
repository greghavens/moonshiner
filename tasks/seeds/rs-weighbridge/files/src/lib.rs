//! rs-weighbridge: weigh-ticket ingest and shift reporting for the Kettle
//! Ridge aggregates yard.
//!
//! Two truck-scale lanes feed one nightly export: the legacy `S1` head on
//! the outbound lane and the newer `S2` head on the inbound lane. Both
//! punch one ticket line per weighment. [`process`] takes the day's ticket
//! lines (the scale house exports them sorted by punch time), buckets them
//! into fixed shift windows (the yard opens at 06:00 and runs 8-hour
//! shifts) and produces billing-grade material totals per shift.

pub mod ingest;
pub mod report;
pub mod window;

/// The yard opens at 06:00; no ticket can be punched earlier.
pub const OPEN_MINUTE: u32 = 6 * 60;

/// Shifts are eight hours wide.
pub const SHIFT_MINUTES: u32 = 8 * 60;

/// Anything that can go wrong between a raw export line and the report.
#[derive(Debug, Clone, PartialEq)]
pub enum PipelineError {
    Ticket(ingest::TicketError),
    Window(window::WindowError),
}

impl std::fmt::Display for PipelineError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PipelineError::Ticket(e) => write!(f, "ticket: {e}"),
            PipelineError::Window(e) => write!(f, "window: {e}"),
        }
    }
}

impl From<ingest::TicketError> for PipelineError {
    fn from(e: ingest::TicketError) -> Self {
        PipelineError::Ticket(e)
    }
}

impl From<window::WindowError> for PipelineError {
    fn from(e: window::WindowError) -> Self {
        PipelineError::Window(e)
    }
}

/// Run the whole pipeline: parse, normalize to kilograms, bucket into
/// shift windows, and reduce to report lines.
pub fn process(lines: &[&str]) -> Result<Vec<report::ReportLine>, PipelineError> {
    let mut windows = window::ShiftWindows::new(OPEN_MINUTE, SHIFT_MINUTES);
    for line in lines {
        let ticket = ingest::parse_ticket(line)?;
        let kg = ingest::to_kg(ticket.net, &ticket.unit)?;
        windows.add(ticket.minute, &ticket.material, kg)?;
    }
    Ok(report::totals(&windows.finish()))
}

/// Convenience wrapper: the rendered plain-text report for a day's export.
pub fn render_report(lines: &[&str]) -> Result<String, PipelineError> {
    Ok(report::render(&process(lines)?))
}
