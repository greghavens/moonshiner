//! Nightly QC report: rank inspection stations by reject rate.
//!
//! Each station reports how many parts it inspected and how many it rejected
//! in the reporting window. The report lists stations worst-first (highest
//! reject rate at the top); stations with equal rates appear alphabetically.
//! `sort_rows` is also used to re-order rows loaded back from snapshot files,
//! so it has to cope with whatever a snapshot contains.

/// One station's counters for the reporting window.
#[derive(Debug, Clone)]
pub struct StationWindow {
    pub station: String,
    pub inspected: u64,
    pub rejected: u64,
}

impl StationWindow {
    pub fn new(station: &str, inspected: u64, rejected: u64) -> Self {
        Self {
            station: station.to_string(),
            inspected,
            rejected,
        }
    }

    /// Fraction of inspected parts that were rejected in the window.
    pub fn reject_rate(&self) -> f64 {
        self.rejected as f64 / self.inspected as f64
    }
}

/// One line of the nightly report.
#[derive(Debug, Clone, PartialEq)]
pub struct ReportRow {
    pub station: String,
    pub rate: f64,
}

/// Order report rows: highest rate first, ties alphabetically by station.
pub fn sort_rows(rows: &mut [ReportRow]) {
    rows.sort_by(|a, b| {
        b.rate
            .partial_cmp(&a.rate)
            .unwrap()
            .then_with(|| a.station.cmp(&b.station))
    });
}

/// Build the nightly report for a set of station windows.
pub fn build_report(windows: &[StationWindow]) -> Vec<ReportRow> {
    let mut rows: Vec<ReportRow> = windows
        .iter()
        .map(|w| ReportRow {
            station: w.station.clone(),
            rate: w.reject_rate(),
        })
        .collect();
    sort_rows(&mut rows);
    rows
}

/// The station most in need of attention, if any windows were reported.
pub fn worst_station(windows: &[StationWindow]) -> Option<String> {
    build_report(windows).into_iter().next().map(|r| r.station)
}
