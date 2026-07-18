//! Public checkpoint facade. Wire values enter and leave here; persistence
//! records remain private to the storage crate.

use checkpoint_protocol::{CommitCode, CommitReply, CommitRequest, StreamStatus};
use checkpoint_storage::{CheckpointStore, CommitError, CommitOutcome};

#[derive(Debug, Default)]
pub struct Gateway {
    store: CheckpointStore,
}

impl Gateway {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn commit(&mut self, request: CommitRequest) -> CommitReply {
        let result = self
            .store
            .commit(&request.stream, request.sequence, &request.digest);
        let code = match result {
            Ok(CommitOutcome::Stored) => CommitCode::Stored,
            Ok(CommitOutcome::Replay) => CommitCode::Replay,
            Err(CommitError::Stale { .. }) => CommitCode::Stale,
            Err(CommitError::Conflict { .. }) => CommitCode::Conflict,
        };
        CommitReply {
            code,
            current_sequence: self
                .store
                .current_sequence(&request.stream)
                .unwrap_or(request.sequence),
        }
    }

    pub fn status(&self, stream: &str) -> Option<StreamStatus> {
        Some(StreamStatus {
            sequence: self.store.current_sequence(stream)?,
            digest: self.store.current_digest(stream)?.to_string(),
        })
    }
}
