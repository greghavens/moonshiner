//! In-memory checkpoint persistence used by the relay daemon and tests.

use std::collections::HashMap;

#[derive(Debug, Clone)]
struct Record {
    sequence: u64,
    digest: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitOutcome {
    Stored,
    Replay,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitError {
    Stale { current: u64 },
    Conflict { current: u64 },
}

#[derive(Debug, Default)]
pub struct CheckpointStore {
    records: HashMap<String, Record>,
    writes: usize,
}

impl CheckpointStore {
    pub fn commit(
        &mut self,
        stream: &str,
        sequence: u64,
        digest: &str,
    ) -> Result<CommitOutcome, CommitError> {
        self.records.insert(
            stream.to_string(),
            Record {
                sequence,
                digest: digest.to_string(),
            },
        );
        self.writes += 1;
        Ok(CommitOutcome::Stored)
    }

    pub fn current_sequence(&self, stream: &str) -> Option<u64> {
        self.records.get(stream).map(|record| record.sequence)
    }

    pub fn current_digest(&self, stream: &str) -> Option<&str> {
        self.records.get(stream).map(|record| record.digest.as_str())
    }

    pub fn write_count(&self) -> usize {
        self.writes
    }
}
