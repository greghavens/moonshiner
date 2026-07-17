//! millreport: plugin-based shift report generator for the rolling mill.
//!
//! A run takes CLI-style flags plus the raw controller feed, aggregates the
//! feed per machine, and drives every selected writer and summary plugin
//! from the registry. Output is returned to the caller (the drop-folder
//! shim is outside this crate), so the whole pipeline is side-effect free.

pub mod config;
pub mod format;
pub mod model;
pub mod pipeline;
pub mod plugin;
pub mod registry;
pub mod summary_alerts;
pub mod summary_totals;
pub mod writer_csv;
pub mod writer_digest;

use std::fmt;

pub use config::{Config, ConfigError};
pub use model::{parse_records, MachineTotals, Metric, ParseError, Record, Report};
pub use pipeline::{run_pipeline, RunOutcome};
pub use registry::{Registry, RegistryError};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RunError {
    Config(ConfigError),
    Records(ParseError),
    Registry(RegistryError),
}

impl fmt::Display for RunError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RunError::Config(e) => write!(f, "{e}"),
            RunError::Records(e) => write!(f, "{e}"),
            RunError::Registry(e) => write!(f, "{e}"),
        }
    }
}

/// CLI-ish entry point: parse flags, parse the feed, run the pipeline.
pub fn run(args: &[&str], input: &str) -> Result<RunOutcome, RunError> {
    let config = Config::parse(args).map_err(RunError::Config)?;
    let records = parse_records(input).map_err(RunError::Records)?;
    let report = Report::build(&config.shift, &records);
    let registry = Registry::with_defaults();
    run_pipeline(&config, &report, &registry).map_err(RunError::Registry)
}
