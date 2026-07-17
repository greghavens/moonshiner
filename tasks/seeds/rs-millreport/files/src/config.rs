//! Run configuration parsed from CLI-style flags.

use std::fmt;

/// Options for one report run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Config {
    /// Shift label stamped on outputs (`--shift`, default `day`).
    pub shift: String,
    /// Writer names to run (`--writers a,b`); `None` means every registered writer.
    pub writers: Option<Vec<String>>,
    /// Summary plugin names to run (`--summaries a,b`); `None` means all.
    pub summaries: Option<Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfigError {
    UnknownFlag(String),
    MissingValue(String),
    DuplicateFlag(String),
    EmptyList(String),
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ConfigError::UnknownFlag(flag) => write!(f, "unknown flag: {flag}"),
            ConfigError::MissingValue(flag) => write!(f, "missing value for {flag}"),
            ConfigError::DuplicateFlag(flag) => write!(f, "duplicate flag: {flag}"),
            ConfigError::EmptyList(flag) => write!(f, "empty list for {flag}"),
        }
    }
}

impl Config {
    /// Parse a flag list. Every flag takes a value; unknown flags are rejected
    /// so a typo never silently changes what a run does.
    pub fn parse(args: &[&str]) -> Result<Config, ConfigError> {
        let mut shift: Option<String> = None;
        let mut writers: Option<Vec<String>> = None;
        let mut summaries: Option<Vec<String>> = None;

        let mut i = 0;
        while i < args.len() {
            let flag = args[i];
            match flag {
                "--shift" => {
                    if shift.is_some() {
                        return Err(ConfigError::DuplicateFlag(flag.to_string()));
                    }
                    shift = Some(take_value(args, i, flag)?.to_string());
                    i += 2;
                }
                "--writers" => {
                    if writers.is_some() {
                        return Err(ConfigError::DuplicateFlag(flag.to_string()));
                    }
                    writers = Some(split_list(flag, take_value(args, i, flag)?)?);
                    i += 2;
                }
                "--summaries" => {
                    if summaries.is_some() {
                        return Err(ConfigError::DuplicateFlag(flag.to_string()));
                    }
                    summaries = Some(split_list(flag, take_value(args, i, flag)?)?);
                    i += 2;
                }
                other => return Err(ConfigError::UnknownFlag(other.to_string())),
            }
        }

        Ok(Config {
            shift: shift.unwrap_or_else(|| "day".to_string()),
            writers,
            summaries,
        })
    }
}

fn take_value<'a>(args: &[&'a str], i: usize, flag: &str) -> Result<&'a str, ConfigError> {
    match args.get(i + 1) {
        Some(value) => Ok(value),
        None => Err(ConfigError::MissingValue(flag.to_string())),
    }
}

fn split_list(flag: &str, raw: &str) -> Result<Vec<String>, ConfigError> {
    let parts: Vec<String> = raw.split(',').map(|p| p.trim().to_string()).collect();
    if raw.trim().is_empty() || parts.iter().any(|p| p.is_empty()) {
        return Err(ConfigError::EmptyList(flag.to_string()));
    }
    Ok(parts)
}
