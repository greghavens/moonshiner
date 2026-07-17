//! Plugin registry: which writers and summaries exist, and in what order.

use std::fmt;

use crate::plugin::{SummaryPlugin, WriterPlugin};
use crate::summary_alerts::AlertsSummary;
use crate::summary_totals::TotalsSummary;
use crate::writer_csv::MachineCsv;
use crate::writer_digest::ShiftDigest;

pub struct Registry {
    writers: Vec<Box<dyn WriterPlugin>>,
    summaries: Vec<Box<dyn SummaryPlugin>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RegistryError {
    UnknownWriter(String),
    UnknownSummary(String),
}

impl fmt::Display for RegistryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RegistryError::UnknownWriter(name) => write!(f, "unknown writer: {name}"),
            RegistryError::UnknownSummary(name) => write!(f, "unknown summary: {name}"),
        }
    }
}

impl Registry {
    /// The stock plant registry. Registration order is the run order.
    pub fn with_defaults() -> Registry {
        Registry {
            writers: vec![Box::new(MachineCsv), Box::new(ShiftDigest)],
            summaries: vec![Box::new(TotalsSummary), Box::new(AlertsSummary)],
        }
    }

    /// Selected writers, always in registry order regardless of how the
    /// selection list was spelled. `None` selects everything.
    pub fn select_writers(
        &self,
        names: &Option<Vec<String>>,
    ) -> Result<Vec<&dyn WriterPlugin>, RegistryError> {
        match names {
            None => Ok(self.writers.iter().map(|w| w.as_ref()).collect()),
            Some(list) => {
                for name in list {
                    if !self.writers.iter().any(|w| w.name() == name) {
                        return Err(RegistryError::UnknownWriter(name.clone()));
                    }
                }
                Ok(self
                    .writers
                    .iter()
                    .filter(|w| list.iter().any(|n| n == w.name()))
                    .map(|w| w.as_ref())
                    .collect())
            }
        }
    }

    /// Selected summary plugins, same rules as writers.
    pub fn select_summaries(
        &self,
        names: &Option<Vec<String>>,
    ) -> Result<Vec<&dyn SummaryPlugin>, RegistryError> {
        match names {
            None => Ok(self.summaries.iter().map(|s| s.as_ref()).collect()),
            Some(list) => {
                for name in list {
                    if !self.summaries.iter().any(|s| s.name() == name) {
                        return Err(RegistryError::UnknownSummary(name.clone()));
                    }
                }
                Ok(self
                    .summaries
                    .iter()
                    .filter(|s| list.iter().any(|n| n == s.name()))
                    .map(|s| s.as_ref())
                    .collect())
            }
        }
    }
}
