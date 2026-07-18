//! Text entrypoint used by the relay sidecar.

use checkpoint_facade::Gateway;
use checkpoint_protocol::{CommitCode, CommitRequest};

pub fn run_line(gateway: &mut Gateway, line: &str) -> String {
    let fields: Vec<&str> = line.split_whitespace().collect();
    if fields.len() != 3 {
        return "error usage: <stream> <sequence> <digest>".to_string();
    }
    let sequence = match fields[1].parse::<u64>() {
        Ok(value) => value,
        Err(_) => return format!("error bad sequence: {}", fields[1]),
    };
    let reply = gateway.commit(CommitRequest {
        stream: fields[0].to_string(),
        sequence,
        digest: fields[2].to_string(),
    });
    match reply.code {
        CommitCode::Stored => format!("stored {} {}", fields[0], sequence),
        CommitCode::Replay => format!("replay {} {}", fields[0], sequence),
        CommitCode::Stale => format!(
            "stale {} {} current={}",
            fields[0], sequence, reply.current_sequence
        ),
        CommitCode::Conflict => format!("conflict {} {}", fields[0], sequence),
    }
}
