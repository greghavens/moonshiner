//! Acceptance contract for rs_scopedproc::process_chunks.
//! Protected file: the implementation must satisfy these tests as written.
//!
//! Synchronization in these tests is channels and mutexes only — no sleeps,
//! no timing assumptions. The few recv_timeout calls are failure backstops
//! so a wrong implementation deadlocks into a clear panic instead of a hang;
//! a correct implementation never waits on them.

use rs_scopedproc::{process_chunks, ChunkError};
use std::sync::mpsc;
use std::sync::Mutex;
use std::thread::ThreadId;
use std::time::Duration;

#[test]
fn sums_match_a_sequential_reference_across_sizes() {
    for len in [0usize, 1, 2, 3, 7, 8, 63, 64, 65, 1000] {
        let items: Vec<u64> = (0..len as u64).map(|i| i * 3 + 1).collect();
        let expected: u64 = items.iter().sum();
        let got = process_chunks(
            &items,
            8,
            3,
            |_, chunk| chunk.iter().sum::<u64>(),
            |a, b| a + b,
        )
        .unwrap();
        if len == 0 {
            assert_eq!(got, None, "len 0 must produce None");
        } else {
            assert_eq!(got, Some(expected), "len {len}");
        }
    }
}

#[test]
fn chunk_boundaries_are_exact_including_ragged_tail() {
    let items: Vec<i32> = (0..10).collect();
    let got = process_chunks(
        &items,
        4,
        2,
        |idx, chunk| vec![(idx, chunk.to_vec())],
        |mut a, mut b| {
            a.append(&mut b);
            a
        },
    )
    .unwrap()
    .unwrap();
    assert_eq!(
        got,
        vec![
            (0, vec![0, 1, 2, 3]),
            (1, vec![4, 5, 6, 7]),
            (2, vec![8, 9]),
        ]
    );
}

#[test]
fn chunk_size_one_processes_every_element_alone() {
    let items = vec![10, 11, 12];
    let got = process_chunks(
        &items,
        1,
        2,
        |idx, chunk| {
            assert_eq!(chunk.len(), 1, "chunk_size 1 means singleton chunks");
            format!("{}:{};", idx, chunk[0])
        },
        |a, b| a + &b,
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, "0:10;1:11;2:12;");
}

#[test]
fn chunk_size_larger_than_input_is_a_single_chunk() {
    let items = vec![5, 6, 7];
    let got = process_chunks(
        &items,
        64,
        4,
        |idx, chunk| (idx, chunk.to_vec()),
        |a, _| a,
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, (0, vec![5, 6, 7]));
}

#[test]
fn empty_input_returns_none_without_calling_map() {
    let items: Vec<u8> = Vec::new();
    let calls = Mutex::new(0usize);
    let got = process_chunks(
        &items,
        16,
        4,
        |_, _| {
            *calls.lock().unwrap() += 1;
        },
        |a, _| a,
    )
    .unwrap();
    assert_eq!(got, None);
    assert_eq!(*calls.lock().unwrap(), 0, "map must never run on empty input");
}

#[test]
fn zero_chunk_size_is_rejected_first() {
    let items = vec![1, 2, 3];
    let empty: Vec<i32> = Vec::new();
    assert_eq!(
        process_chunks(&items, 0, 2, |_, c| c.len(), |a, b| a + b),
        Err(ChunkError::ZeroChunkSize)
    );
    assert_eq!(
        process_chunks(&empty, 0, 2, |_, c| c.len(), |a, b| a + b),
        Err(ChunkError::ZeroChunkSize),
        "validation precedes the empty-input shortcut"
    );
    assert_eq!(
        process_chunks(&items, 0, 0, |_, c| c.len(), |a, b| a + b),
        Err(ChunkError::ZeroChunkSize),
        "chunk size is checked before workers"
    );
}

#[test]
fn zero_workers_is_rejected() {
    let items = vec![1, 2, 3];
    let empty: Vec<i32> = Vec::new();
    assert_eq!(
        process_chunks(&items, 2, 0, |_, c| c.len(), |a, b| a + b),
        Err(ChunkError::ZeroWorkers)
    );
    assert_eq!(
        process_chunks(&empty, 2, 0, |_, c| c.len(), |a, b| a + b),
        Err(ChunkError::ZeroWorkers)
    );
}

#[test]
fn merge_is_a_left_fold_in_chunk_index_order() {
    let items: Vec<u32> = (0..8).collect();
    // Non-commutative, non-associative merge: parenthesization exposes both
    // the fold direction and the operand order.
    let got = process_chunks(
        &items,
        2,
        3,
        |idx, _| format!("c{idx}"),
        |a, b| format!("({a}+{b})"),
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, "(((c0+c1)+c2)+c3)");
}

#[test]
fn chunks_are_assigned_round_robin_and_run_in_order_per_worker() {
    let items: Vec<u8> = vec![0; 12]; // 6 chunks of 2
    let log: Mutex<Vec<(usize, ThreadId)>> = Mutex::new(Vec::new());
    process_chunks(
        &items,
        2,
        2,
        |idx, _| {
            log.lock().unwrap().push((idx, std::thread::current().id()));
        },
        |a, _| a,
    )
    .unwrap();

    let log = log.into_inner().unwrap();
    assert_eq!(log.len(), 6);
    let id_of = |chunk: usize| log.iter().find(|(i, _)| *i == chunk).unwrap().1;

    // worker 0 owns chunks 0,2,4 — worker 1 owns 1,3,5
    assert_eq!(id_of(0), id_of(2));
    assert_eq!(id_of(2), id_of(4));
    assert_eq!(id_of(1), id_of(3));
    assert_eq!(id_of(3), id_of(5));
    assert_ne!(id_of(0), id_of(1), "two workers means two distinct threads");

    // each worker walks its chunks in increasing order
    for group in [[0usize, 2, 4], [1, 3, 5]] {
        let seen: Vec<usize> = log
            .iter()
            .filter(|(i, _)| group.contains(i))
            .map(|(i, _)| *i)
            .collect();
        assert_eq!(seen, group.to_vec(), "per-worker chunk order must ascend");
    }
}

#[test]
fn merge_order_is_chunk_order_even_when_completion_is_scrambled() {
    let items: Vec<u8> = vec![0; 4]; // 4 chunks of 1
    let (tx, rx) = mpsc::channel::<()>();
    let gate_rx = Mutex::new(Some(rx));
    let gate_tx = Mutex::new(Some(tx));

    // Chunk 0 (worker 0) blocks until chunk 1 (worker 1) has finished, so
    // completion order is provably not chunk order.
    let got = process_chunks(
        &items,
        1,
        2,
        |idx, _| {
            if idx == 0 {
                let rx = gate_rx.lock().unwrap().take().expect("gate taken once");
                rx.recv_timeout(Duration::from_secs(10)).expect(
                    "chunk 0 must be gated by chunk 1 completing on the other \
                     worker — is assignment round-robin?",
                );
            }
            if idx == 1 {
                gate_tx
                    .lock()
                    .unwrap()
                    .take()
                    .expect("gate fired once")
                    .send(())
                    .expect("chunk 0 still waiting");
            }
            format!("c{idx};")
        },
        |a, b| a + &b,
    )
    .unwrap()
    .unwrap();

    assert_eq!(got, "c0;c1;c2;c3;", "merge order is chunk order, not completion order");
}

#[test]
fn single_worker_runs_all_chunks_in_order() {
    let items: Vec<u16> = (0..10).collect();
    let order: Mutex<Vec<usize>> = Mutex::new(Vec::new());
    let got = process_chunks(
        &items,
        3,
        1,
        |idx, chunk| {
            order.lock().unwrap().push(idx);
            chunk.iter().map(|v| *v as u64).sum::<u64>()
        },
        |a, b| a + b,
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, 45);
    assert_eq!(*order.lock().unwrap(), vec![0, 1, 2, 3]);
}

#[test]
fn map_results_may_borrow_from_the_input() {
    let items: Vec<u32> = vec![40, 7, 99, 13, 58, 99, 2];
    let max = process_chunks(
        &items,
        3,
        2,
        |_, chunk| chunk.iter().max().expect("chunks are never empty"),
        |a, b| if *b > *a { b } else { a },
    )
    .unwrap()
    .unwrap();
    assert_eq!(*max, 99);
    assert!(
        std::ptr::eq(max, &items[2]),
        "the result must borrow the winning element (first max wins merge ties)"
    );
}

#[derive(Debug, PartialEq)]
struct Reading {
    celsius: i64, // deliberately neither Clone nor Copy nor Default
}

#[test]
fn works_with_non_clone_non_default_elements() {
    let items: Vec<Reading> = (0..25).map(|i| Reading { celsius: i - 5 }).collect();
    let got = process_chunks(
        &items,
        4,
        3,
        |_, chunk| chunk.iter().map(|r| r.celsius).sum::<i64>(),
        |a, b| a + b,
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, (0..25).map(|i| i - 5).sum::<i64>());
}

#[test]
fn large_input_matches_sequential_reference() {
    let items: Vec<u64> = (0..10_000u64)
        .map(|i| i.wrapping_mul(2654435761) % 997)
        .collect();
    let expected: u64 = items.iter().sum();
    let chunks_seen = Mutex::new(0usize);
    let got = process_chunks(
        &items,
        97,
        4,
        |_, chunk| {
            *chunks_seen.lock().unwrap() += 1;
            chunk.iter().sum::<u64>()
        },
        |a, b| a + b,
    )
    .unwrap()
    .unwrap();
    assert_eq!(got, expected);
    assert_eq!(*chunks_seen.lock().unwrap(), 10_000usize.div_ceil(97));
}

#[test]
fn workers_are_capped_at_the_chunk_count() {
    let items: Vec<u8> = vec![1; 6]; // 3 chunks of 2
    let log: Mutex<Vec<ThreadId>> = Mutex::new(Vec::new());
    process_chunks(
        &items,
        2,
        16,
        |_, _| {
            log.lock().unwrap().push(std::thread::current().id());
        },
        |a, _| a,
    )
    .unwrap();
    let ids: std::collections::HashSet<ThreadId> =
        log.into_inner().unwrap().into_iter().collect();
    assert_eq!(
        ids.len(),
        3,
        "min(workers, chunks) = 3 workers, each owning exactly one chunk"
    );
}

#[test]
fn repeated_runs_are_identical() {
    let items: Vec<u32> = (0..40).collect();
    let mut outputs = Vec::new();
    for _ in 0..20 {
        let got = process_chunks(
            &items,
            7,
            3,
            |idx, chunk| format!("[{idx}:{}]", chunk.iter().sum::<u32>()),
            |a, b| a + &b,
        )
        .unwrap()
        .unwrap();
        outputs.push(got);
    }
    outputs.dedup();
    assert_eq!(outputs.len(), 1, "same input must give byte-identical output every run");
}
