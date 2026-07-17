//! Record feed parsing and per-shift aggregation.

use std::collections::BTreeMap;
use std::fmt;

/// One line from the stand controllers: `machine,metric,value`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub machine: String,
    pub metric: Metric,
    pub value: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Metric {
    Produced,
    DowntimeMin,
    Scrap,
}

impl Metric {
    fn from_key(key: &str) -> Option<Metric> {
        match key {
            "produced" => Some(Metric::Produced),
            "downtime_min" => Some(Metric::DowntimeMin),
            "scrap" => Some(Metric::Scrap),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub line: usize,
    pub reason: String,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "line {}: {}", self.line, self.reason)
    }
}

/// Parse the raw feed. Blank lines and `#` comments are skipped; line numbers
/// in errors refer to the raw input, 1-based.
pub fn parse_records(input: &str) -> Result<Vec<Record>, ParseError> {
    let mut records = Vec::new();
    for (idx, raw) in input.lines().enumerate() {
        let line_no = idx + 1;
        let line = raw.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let fields: Vec<&str> = line.split(',').collect();
        if fields.len() != 3 {
            return Err(ParseError {
                line: line_no,
                reason: "expected machine,metric,value".to_string(),
            });
        }
        let machine = fields[0].trim();
        if machine.is_empty() {
            return Err(ParseError {
                line: line_no,
                reason: "blank machine id".to_string(),
            });
        }
        let metric = Metric::from_key(fields[1].trim()).ok_or_else(|| ParseError {
            line: line_no,
            reason: format!("unknown metric {}", fields[1].trim()),
        })?;
        let value: u64 = fields[2].trim().parse().map_err(|_| ParseError {
            line: line_no,
            reason: format!("bad value {}", fields[2].trim()),
        })?;
        records.push(Record {
            machine: machine.to_string(),
            metric,
            value,
        });
    }
    Ok(records)
}

/// Aggregated totals for one machine.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MachineTotals {
    pub produced: u64,
    pub downtime_min: u64,
    pub scrap: u64,
}

/// The aggregated shift report every plugin renders from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    pub shift: String,
    /// Per-machine totals, ordered by machine id.
    pub machines: BTreeMap<String, MachineTotals>,
}

impl Report {
    pub fn build(shift: &str, records: &[Record]) -> Report {
        let mut machines: BTreeMap<String, MachineTotals> = BTreeMap::new();
        for record in records {
            let totals = machines.entry(record.machine.clone()).or_default();
            match record.metric {
                Metric::Produced => totals.produced += record.value,
                Metric::DowntimeMin => totals.downtime_min += record.value,
                Metric::Scrap => totals.scrap += record.value,
            }
        }
        Report {
            shift: shift.to_string(),
            machines,
        }
    }

    /// Whole-shift totals across every machine.
    pub fn totals(&self) -> MachineTotals {
        let mut sum = MachineTotals::default();
        for totals in self.machines.values() {
            sum.produced += totals.produced;
            sum.downtime_min += totals.downtime_min;
            sum.scrap += totals.scrap;
        }
        sum
    }
}
