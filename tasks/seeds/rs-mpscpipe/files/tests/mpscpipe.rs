//! Acceptance contract for rs_mpscpipe::{Pipeline, PipeError}.
//! Protected file: the implementation must satisfy these tests as written.
//!
//! Synchronization in these tests is channels and mutexes only — no sleeps,
//! no timing assumptions. Every recv_timeout is a failure backstop so a
//! wrong implementation panics with a clear message instead of hanging;
//! a correct implementation never waits on one.

use rs_mpscpipe::{PipeError, Pipeline};
use std::collections::HashSet;
use std::sync::mpsc::{self, TryRecvError};
use std::sync::{Arc, Mutex};
use std::thread::{self, ThreadId};
use std::time::Duration;

const BACKSTOP: Duration = Duration::from_secs(10);

#[test]
fn zero_workers_is_rejected() {
    let built = Pipeline::new(0, |_, item: u32| item);
    assert_eq!(built.err(), Some(PipeError::ZeroWorkers));
}

#[test]
fn submit_returns_consecutive_indices_from_zero() {
    let mut pipe = Pipeline::new(2, |_, item: u32| item).unwrap();
    assert_eq!(pipe.submit(90), 0);
    assert_eq!(pipe.submit(91), 1);
    assert_eq!(pipe.submit(92), 2);
    assert_eq!(pipe.finish().unwrap(), vec![90, 91, 92]);
}

#[test]
fn single_worker_processes_in_submission_order() {
    let seen: Arc<Mutex<Vec<u64>>> = Arc::new(Mutex::new(Vec::new()));
    let mut pipe = Pipeline::new(1, {
        let seen = Arc::clone(&seen);
        move |idx, item: u32| {
            seen.lock().unwrap().push(idx);
            item * 2
        }
    })
    .unwrap();
    for item in [5u32, 6, 7, 8, 9] {
        pipe.submit(item);
    }
    assert_eq!(pipe.finish().unwrap(), vec![10, 12, 14, 16, 18]);
    assert_eq!(
        *seen.lock().unwrap(),
        vec![0, 1, 2, 3, 4],
        "one worker drains the queue front to back"
    );
}

#[test]
fn results_are_reassembled_in_submission_order_under_scrambled_completion() {
    // Job 0 blocks until job 1 has completed on the other worker, so
    // completion order is provably not submission order.
    let (gate_tx, gate_rx) = mpsc::channel::<()>();
    let gate_rx = Arc::new(Mutex::new(Some(gate_rx)));
    let gate_tx = Arc::new(Mutex::new(Some(gate_tx)));
    let completed: Arc<Mutex<Vec<u64>>> = Arc::new(Mutex::new(Vec::new()));

    let mut pipe = Pipeline::new(2, {
        let gate_rx = Arc::clone(&gate_rx);
        let gate_tx = Arc::clone(&gate_tx);
        let completed = Arc::clone(&completed);
        move |idx, item: u32| {
            if idx == 0 {
                let rx = gate_rx.lock().unwrap().take().expect("gate taken once");
                rx.recv_timeout(BACKSTOP).expect(
                    "job 0 must be gated by job 1 finishing on the other worker",
                );
            }
            completed.lock().unwrap().push(idx);
            if idx == 1 {
                gate_tx
                    .lock()
                    .unwrap()
                    .take()
                    .expect("gate fired once")
                    .send(())
                    .expect("job 0 is waiting");
            }
            item + 100
        }
    })
    .unwrap();

    for item in [0u32, 1, 2, 3] {
        pipe.submit(item);
    }
    assert_eq!(
        pipe.finish().unwrap(),
        vec![100, 101, 102, 103],
        "output order is submission order, not completion order"
    );
    let completed = completed.lock().unwrap();
    let pos = |wanted: u64| completed.iter().position(|&i| i == wanted).unwrap();
    assert!(pos(1) < pos(0), "the gate forces job 1 to complete before job 0");
}

#[test]
fn a_three_stage_scramble_chain_still_reassembles() {
    // Completion of the first three jobs is forced to 2, then 1, then 0.
    let (a_tx, a_rx) = mpsc::channel::<()>();
    let (b_tx, b_rx) = mpsc::channel::<()>();
    let gates_rx = Arc::new(Mutex::new([Some(a_rx), Some(b_rx)]));
    let gates_tx = Arc::new(Mutex::new([Some(a_tx), Some(b_tx)]));
    let completed: Arc<Mutex<Vec<u64>>> = Arc::new(Mutex::new(Vec::new()));

    let mut pipe = Pipeline::new(3, {
        let gates_rx = Arc::clone(&gates_rx);
        let gates_tx = Arc::clone(&gates_tx);
        let completed = Arc::clone(&completed);
        move |idx, item: u64| {
            match idx {
                0 => {
                    let rx = gates_rx.lock().unwrap()[0].take().expect("gate a once");
                    rx.recv_timeout(BACKSTOP).expect("job 0 waits for job 1");
                }
                1 => {
                    let rx = gates_rx.lock().unwrap()[1].take().expect("gate b once");
                    rx.recv_timeout(BACKSTOP).expect("job 1 waits for job 2");
                }
                _ => {}
            }
            completed.lock().unwrap().push(idx);
            match idx {
                1 => gates_tx.lock().unwrap()[0]
                    .take()
                    .expect("gate a fired once")
                    .send(())
                    .expect("job 0 is waiting"),
                2 => gates_tx.lock().unwrap()[1]
                    .take()
                    .expect("gate b fired once")
                    .send(())
                    .expect("job 1 is waiting"),
                _ => {}
            }
            item * 11
        }
    })
    .unwrap();

    for item in [1u64, 2, 3, 4, 5, 6] {
        pipe.submit(item);
    }
    assert_eq!(pipe.finish().unwrap(), vec![11, 22, 33, 44, 55, 66]);
    let completed = completed.lock().unwrap();
    let pos = |wanted: u64| completed.iter().position(|&i| i == wanted).unwrap();
    assert!(pos(2) < pos(1) && pos(1) < pos(0), "forced completion order 2, 1, 0");
}

#[test]
fn concurrent_jobs_run_on_distinct_worker_threads() {
    // Three jobs rendezvous: none may finish until all three have started,
    // which requires three live worker threads.
    let (go_txs, go_rxs): (Vec<_>, Vec<_>) = (0..3).map(|_| mpsc::channel::<()>()).unzip();
    let launcher = Arc::new(Mutex::new((0usize, Some(go_txs))));
    let gates: Arc<Mutex<Vec<Option<mpsc::Receiver<()>>>>> =
        Arc::new(Mutex::new(go_rxs.into_iter().map(Some).collect()));
    let ids: Arc<Mutex<Vec<ThreadId>>> = Arc::new(Mutex::new(Vec::new()));

    let mut pipe = Pipeline::new(3, {
        let launcher = Arc::clone(&launcher);
        let gates = Arc::clone(&gates);
        let ids = Arc::clone(&ids);
        move |idx, _item: u8| {
            ids.lock().unwrap().push(thread::current().id());
            let gate = gates.lock().unwrap()[idx as usize].take().expect("one job per gate");
            {
                let mut state = launcher.lock().unwrap();
                state.0 += 1;
                if state.0 == 3 {
                    for tx in state.1.take().expect("launch exactly once") {
                        tx.send(()).expect("every gate receiver is alive");
                    }
                }
            }
            gate.recv_timeout(BACKSTOP).expect(
                "all 3 jobs must be in flight at once — are there really 3 workers?",
            );
        }
    })
    .unwrap();

    for _ in 0..3 {
        pipe.submit(0u8);
    }
    pipe.finish().unwrap();

    let distinct: HashSet<ThreadId> = ids.lock().unwrap().iter().copied().collect();
    assert_eq!(distinct.len(), 3, "each concurrent job runs on its own worker thread");
}

#[test]
fn job_panic_becomes_a_typed_error_naming_the_item() {
    // Run the whole pipeline on a helper thread so a hanging finish() fails
    // fast with a clear message instead of stalling the suite.
    let (done_tx, done_rx) = mpsc::channel();
    thread::spawn(move || {
        let mut pipe = Pipeline::new(2, |idx, item: u32| {
            if idx == 2 {
                panic!("boom item {item}");
            }
            item * 10
        })
        .unwrap();
        for item in [10u32, 11, 12, 13, 14] {
            pipe.submit(item);
        }
        done_tx.send(pipe.finish()).unwrap();
    });
    let outcome = done_rx
        .recv_timeout(BACKSTOP)
        .expect("finish() must not hang when a job panics");
    assert_eq!(
        outcome,
        Err(PipeError::JobPanicked { index: 2, message: "boom item 12".to_string() })
    );
}

#[test]
fn a_panicking_job_does_not_stop_the_others() {
    let completed: Arc<Mutex<Vec<u64>>> = Arc::new(Mutex::new(Vec::new()));
    let mut pipe = Pipeline::new(2, {
        let completed = Arc::clone(&completed);
        move |idx, item: u32| {
            if idx == 2 {
                panic!("job {idx} went down");
            }
            completed.lock().unwrap().push(idx);
            item
        }
    })
    .unwrap();
    for item in 0..6u32 {
        pipe.submit(item);
    }
    assert!(matches!(
        pipe.finish(),
        Err(PipeError::JobPanicked { index: 2, .. })
    ));
    let mut done = completed.lock().unwrap().clone();
    done.sort_unstable();
    assert_eq!(
        done,
        vec![0, 1, 3, 4, 5],
        "every non-panicking job still runs to completion"
    );
}

#[test]
fn multiple_panics_report_the_lowest_submission_index() {
    let mut pipe = Pipeline::new(3, |idx, item: u32| {
        if idx == 5 || idx == 2 {
            panic!("bad item {item}");
        }
        item
    })
    .unwrap();
    for item in 20..28u32 {
        pipe.submit(item);
    }
    assert_eq!(
        pipe.finish(),
        Err(PipeError::JobPanicked { index: 2, message: "bad item 22".to_string() }),
        "ties break toward the earliest submission, regardless of completion order"
    );
}

#[test]
fn panic_payload_kinds_are_stringified() {
    // &'static str payload.
    let mut pipe = Pipeline::new(1, |_, _item: u8| -> u8 { panic!("plain literal") }).unwrap();
    pipe.submit(0u8);
    assert_eq!(
        pipe.finish(),
        Err(PipeError::JobPanicked { index: 0, message: "plain literal".to_string() })
    );

    // Non-string payload falls back to a fixed marker.
    let mut pipe = Pipeline::new(1, |_, _item: u8| -> u8 {
        std::panic::panic_any(7usize)
    })
    .unwrap();
    pipe.submit(0u8);
    assert_eq!(
        pipe.finish(),
        Err(PipeError::JobPanicked { index: 0, message: "<non-string panic>".to_string() })
    );
}

#[test]
fn finishing_with_no_submissions_is_ok_and_empty() {
    let pipe = Pipeline::new(4, |_, item: u8| item).unwrap();
    assert_eq!(pipe.finish().unwrap(), Vec::<u8>::new());
}

#[test]
fn finish_joins_workers_and_drops_the_job_closure() {
    let (probe_tx, probe_rx) = mpsc::channel::<()>();
    let mut pipe = Pipeline::new(3, move |_, item: u32| {
        let _keep_alive = &probe_tx;
        item + 1
    })
    .unwrap();
    for item in 0..10u32 {
        pipe.submit(item);
    }
    assert_eq!(pipe.finish().unwrap(), (1..11u32).collect::<Vec<_>>());
    assert_eq!(
        probe_rx.try_recv(),
        Err(TryRecvError::Disconnected),
        "after finish() the job closure must be gone — all workers joined, \
         nothing still holding it"
    );
}

#[test]
fn workers_are_joined_even_when_a_job_panicked() {
    let (probe_tx, probe_rx) = mpsc::channel::<()>();
    let mut pipe = Pipeline::new(2, move |idx, item: u32| {
        let _keep_alive = &probe_tx;
        if idx == 1 {
            panic!("down");
        }
        item
    })
    .unwrap();
    for item in 0..4u32 {
        pipe.submit(item);
    }
    assert!(matches!(
        pipe.finish(),
        Err(PipeError::JobPanicked { index: 1, .. })
    ));
    assert_eq!(
        probe_rx.try_recv(),
        Err(TryRecvError::Disconnected),
        "the error path must still join every worker and drop the closure"
    );
}

#[derive(Debug, PartialEq)]
struct Manifest {
    name: String, // deliberately neither Clone nor Copy nor Default
}

#[derive(Debug, PartialEq)]
struct Stamped {
    tag: String,
}

#[test]
fn non_clone_items_and_results_move_through() {
    let mut pipe = Pipeline::new(2, |idx, item: Manifest| Stamped {
        tag: format!("{}#{}", item.name, idx),
    })
    .unwrap();
    for name in ["alpha", "beta", "gamma"] {
        pipe.submit(Manifest { name: name.to_string() });
    }
    assert_eq!(
        pipe.finish().unwrap(),
        vec![
            Stamped { tag: "alpha#0".to_string() },
            Stamped { tag: "beta#1".to_string() },
            Stamped { tag: "gamma#2".to_string() },
        ]
    );
}

#[test]
fn large_batch_matches_a_sequential_reference() {
    let items: Vec<u64> = (0..1_000u64)
        .map(|i| i.wrapping_mul(2654435761) % 9973)
        .collect();
    let expected: Vec<u64> = items
        .iter()
        .enumerate()
        .map(|(idx, item)| item.wrapping_mul(31).wrapping_add(idx as u64))
        .collect();

    let mut pipe =
        Pipeline::new(4, |idx, item: u64| item.wrapping_mul(31).wrapping_add(idx)).unwrap();
    for item in &items {
        pipe.submit(*item);
    }
    assert_eq!(pipe.finish().unwrap(), expected);
}

#[test]
fn job_fn_receives_the_matching_index_and_item() {
    let pairs: Arc<Mutex<Vec<(u64, u32)>>> = Arc::new(Mutex::new(Vec::new()));
    let mut pipe = Pipeline::new(3, {
        let pairs = Arc::clone(&pairs);
        move |idx, item: u32| {
            pairs.lock().unwrap().push((idx, item));
        }
    })
    .unwrap();
    for i in 0..8u32 {
        pipe.submit(100 + i);
    }
    pipe.finish().unwrap();
    let mut pairs = pairs.lock().unwrap().clone();
    pairs.sort_unstable();
    let expected: Vec<(u64, u32)> = (0..8).map(|i| (i as u64, 100 + i as u32)).collect();
    assert_eq!(pairs, expected, "index i must arrive with the i-th submitted item");
}
