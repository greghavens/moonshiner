//! Generated relay checkpoint contract. Keep this crate storage-agnostic.

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitRequest {
    pub stream: String,
    pub sequence: u64,
    pub digest: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommitCode {
    Stored,
    Replay,
    Stale,
    Conflict,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommitReply {
    pub code: CommitCode,
    pub current_sequence: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StreamStatus {
    pub sequence: u64,
    pub digest: String,
}
