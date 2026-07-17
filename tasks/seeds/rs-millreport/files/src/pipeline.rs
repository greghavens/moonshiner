//! Drives the selected plugins over one aggregated report.

use std::collections::BTreeMap;

use crate::config::Config;
use crate::model::Report;
use crate::registry::{Registry, RegistryError};

/// What one run produced: rendered files by name, plus the run summary lines.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RunOutcome {
    pub files: BTreeMap<String, String>,
    pub summary: Vec<String>,
}

pub fn run_pipeline(
    config: &Config,
    report: &Report,
    registry: &Registry,
) -> Result<RunOutcome, RegistryError> {
    let writers = registry.select_writers(&config.writers)?;
    let summaries = registry.select_summaries(&config.summaries)?;

    let mut files = BTreeMap::new();
    let mut summary = Vec::new();

    for writer in &writers {
        files.insert(writer.filename(report), writer.render(report));
    }
    for plugin in &summaries {
        summary.extend(plugin.lines(report));
    }

    Ok(RunOutcome { files, summary })
}
