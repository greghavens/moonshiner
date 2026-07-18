use checkpoint_agent::run_line;
use checkpoint_facade::Gateway;
use checkpoint_protocol::{CommitCode, CommitReply, CommitRequest};
use checkpoint_storage::{CheckpointStore, CommitError, CommitOutcome};

fn request(stream: &str, sequence: u64, digest: &str) -> CommitRequest {
    CommitRequest {
        stream: stream.to_string(),
        sequence,
        digest: digest.to_string(),
    }
}

#[test]
fn storage_owner_rejects_an_older_sequence_without_a_write() {
    let mut store = CheckpointStore::default();
    assert_eq!(store.commit("north-yard", 41, "sha-a"), Ok(CommitOutcome::Stored));
    assert_eq!(store.write_count(), 1);

    assert_eq!(
        store.commit("north-yard", 39, "sha-old"),
        Err(CommitError::Stale { current: 41 })
    );
    assert_eq!(store.current_sequence("north-yard"), Some(41));
    assert_eq!(store.current_digest("north-yard"), Some("sha-a"));
    assert_eq!(store.write_count(), 1, "a stale request must not persist");
}

#[test]
fn storage_distinguishes_replay_from_conflict_and_can_still_advance() {
    let mut store = CheckpointStore::default();
    assert_eq!(store.commit("south-yard", 8, "sha-8"), Ok(CommitOutcome::Stored));
    assert_eq!(store.commit("south-yard", 8, "sha-8"), Ok(CommitOutcome::Replay));
    assert_eq!(store.write_count(), 1, "an exact replay is read-only");

    assert_eq!(
        store.commit("south-yard", 8, "sha-other"),
        Err(CommitError::Conflict { current: 8 })
    );
    assert_eq!(store.current_digest("south-yard"), Some("sha-8"));
    assert_eq!(store.write_count(), 1, "a conflicting digest is read-only");

    assert_eq!(store.commit("south-yard", 13, "sha-13"), Ok(CommitOutcome::Stored));
    assert_eq!(store.current_sequence("south-yard"), Some(13));
    assert_eq!(store.write_count(), 2);
}

#[test]
fn facade_keeps_protocol_types_and_state_consistent() {
    let signature: fn(&mut Gateway, CommitRequest) -> CommitReply = Gateway::commit;
    let mut gateway = Gateway::new();
    assert_eq!(signature(&mut gateway, request("west", 7, "dg-7")).code, CommitCode::Stored);
    assert_eq!(gateway.commit(request("west", 6, "dg-6")).code, CommitCode::Stale);
    assert_eq!(gateway.commit(request("west", 7, "different")).code, CommitCode::Conflict);
    assert_eq!(gateway.commit(request("west", 7, "dg-7")).code, CommitCode::Replay);

    let status = gateway.status("west").expect("committed status");
    assert_eq!((status.sequence, status.digest.as_str()), (7, "dg-7"));
}

#[test]
fn binary_route_reports_the_shared_decision_without_suppressing_functionality() {
    let mut gateway = Gateway::new();
    assert_eq!(run_line(&mut gateway, "east 100 alpha"), "stored east 100");
    assert_eq!(run_line(&mut gateway, "east 100 alpha"), "replay east 100");
    assert_eq!(run_line(&mut gateway, "east 100 beta"), "conflict east 100");
    assert_eq!(
        run_line(&mut gateway, "east 99 old"),
        "stale east 99 current=100"
    );
    assert_eq!(run_line(&mut gateway, "east 101 next"), "stored east 101");
    assert_eq!(gateway.status("east").unwrap().digest, "next");
}

#[test]
fn streams_are_independent_and_bad_cli_input_never_commits() {
    let mut gateway = Gateway::new();
    assert_eq!(run_line(&mut gateway, "alpha 900 a"), "stored alpha 900");
    assert_eq!(run_line(&mut gateway, "beta 2 b"), "stored beta 2");
    assert_eq!(gateway.status("alpha").unwrap().sequence, 900);
    assert_eq!(gateway.status("beta").unwrap().sequence, 2);

    assert_eq!(run_line(&mut gateway, "beta nope x"), "error bad sequence: nope");
    assert_eq!(run_line(&mut gateway, "only-two 3"), "error usage: <stream> <sequence> <digest>");
    assert_eq!(gateway.status("beta").unwrap().sequence, 2);
    assert!(gateway.status("only-two").is_none());
}
