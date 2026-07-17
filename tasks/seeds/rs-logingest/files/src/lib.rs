//! Log ingest pipeline: parse agent log lines, normalize timestamps to UTC,
//! drop duplicate events, and hand records to the batching output sink.
//!
//! Line format (one event per line, blank lines ignored):
//!
//! ```text
//! 2026-03-14T22:10:05+01:00 host=web-1 level=error msg="db timeout"
//! ```
//!
//! The timestamp offset suffix is optional: `Z` means UTC, `+HH:MM`/`-HH:MM`
//! is an explicit offset, and a bare timestamp is agent-local wall time that
//! must be interpreted using the configured fleet offset.

mod dedup;
mod parse;
mod sink;

use std::collections::HashSet;

/// Static configuration for one ingest run.
#[derive(Debug, Clone)]
pub struct IngestConfig {
    /// Offset (minutes east of UTC) assumed for timestamps that carry no
    /// explicit offset — the agent fleet's local wall-clock offset.
    pub default_offset_minutes: i32,
    /// Records are moved to the output in blocks of this size (the real
    /// system writes each block with one syscall).
    pub batch_size: usize,
}

/// The result of a successful ingest run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IngestOutput {
    /// Normalized records, in input order: `"{epoch} {host} {level} {msg}"`.
    pub records: Vec<String>,
    /// Events kept after de-duplication.
    pub accepted: usize,
    /// Events dropped as duplicates.
    pub duplicates: usize,
}

/// A malformed input line; `line` is 1-based.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub line: usize,
    pub msg: String,
}

impl std::fmt::Display for ParseError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "line {}: {}", self.line, self.msg)
    }
}

impl std::error::Error for ParseError {}

/// Run the full pipeline over one input document.
pub fn ingest(input: &str, cfg: &IngestConfig) -> Result<IngestOutput, ParseError> {
    let mut seen: HashSet<String> = HashSet::new();
    let mut sink = sink::BatchSink::new(cfg.batch_size);
    let mut accepted = 0usize;
    let mut duplicates = 0usize;

    for (idx, raw) in input.lines().enumerate() {
        if raw.trim().is_empty() {
            continue;
        }
        let rec = parse::parse_line(raw, idx + 1, cfg)?;
        if !seen.insert(dedup::dedup_key(&rec)) {
            duplicates += 1;
            continue;
        }
        accepted += 1;
        sink.push(format!(
            "{} {} {} {}",
            rec.epoch, rec.host, rec.level, rec.msg
        ));
    }

    Ok(IngestOutput {
        records: sink.finish(),
        accepted,
        duplicates,
    })
}
