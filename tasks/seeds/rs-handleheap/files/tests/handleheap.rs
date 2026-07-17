//! Acceptance contract for the rs_handleheap dispatch heap.
//! Protected file: the implementation must satisfy these tests as written.

use rs_handleheap::{Handle, HandleHeap, HeapError};

/// Deterministic 64-bit LCG (MMIX constants) — the scripted test replays
/// the exact same op sequence every run.
struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> u64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        self.0 >> 33
    }
    fn below(&mut self, n: u64) -> u64 {
        self.next() % n
    }
}

/// Drain the heap, returning (priority, value) in pop order.
fn drain<T>(h: &mut HandleHeap<T>) -> Vec<(u64, T)> {
    let mut out = Vec::new();
    while let Some((_, p, v)) = h.pop() {
        out.push((p, v));
    }
    out
}

#[test]
fn push_peek_pop_smallest_first() {
    let mut h = HandleHeap::new();
    assert!(h.is_empty());
    let hb = h.push(20, "b".to_string());
    let ha = h.push(10, "a".to_string());
    assert_eq!(h.len(), 2);

    let (peek_h, peek_p, peek_v) = h.peek().expect("non-empty heap must peek");
    assert_eq!((peek_h, peek_p, peek_v.as_str()), (ha, 10, "a"));

    let (pop_h, pop_p, pop_v) = h.pop().unwrap();
    assert_eq!((pop_h, pop_p, pop_v.as_str()), (ha, 10, "a"));
    let (pop_h, pop_p, pop_v) = h.pop().unwrap();
    assert_eq!((pop_h, pop_p, pop_v.as_str()), (hb, 20, "b"));
    assert_eq!(h.pop(), None);
    assert!(h.is_empty());
}

#[test]
fn pop_order_across_mixed_priorities() {
    let mut h = HandleHeap::new();
    for (p, v) in [(50, "e"), (10, "a"), (40, "d"), (20, "b"), (30, "c")] {
        h.push(p, v.to_string());
    }
    let got = drain(&mut h);
    let expected: Vec<(u64, String)> = [(10, "a"), (20, "b"), (30, "c"), (40, "d"), (50, "e")]
        .iter()
        .map(|(p, v)| (*p, v.to_string()))
        .collect();
    assert_eq!(got, expected);
}

#[test]
fn equal_priorities_pop_in_insertion_order() {
    let mut h = HandleHeap::new();
    // Interleave two priority classes; within each class, insertion order rules.
    h.push(2, "a1".to_string());
    h.push(1, "x1".to_string());
    h.push(2, "a2".to_string());
    h.push(1, "x2".to_string());
    h.push(2, "a3".to_string());
    h.push(1, "x3".to_string());
    let got = drain(&mut h);
    let expected: Vec<(u64, String)> = [
        (1, "x1"),
        (1, "x2"),
        (1, "x3"),
        (2, "a1"),
        (2, "a2"),
        (2, "a3"),
    ]
    .iter()
    .map(|(p, v)| (*p, v.to_string()))
    .collect();
    assert_eq!(got, expected, "FIFO within a priority class is the fairness SLA");
}

#[test]
fn fifo_rank_is_global_enqueue_order_across_pops_and_pushes() {
    let mut h = HandleHeap::new();
    h.push(5, "first");
    h.push(5, "second");
    assert_eq!(h.pop().map(|(_, p, v)| (p, v)), Some((5, "first")));
    h.push(5, "third"); // enqueued after "second", must pop after it
    h.push(4, "vip");
    assert_eq!(
        drain(&mut h),
        vec![(4, "vip"), (5, "second"), (5, "third")]
    );
}

#[test]
fn peek_does_not_consume_and_matches_next_pop() {
    let mut h = HandleHeap::new();
    h.push(3, 30u32);
    h.push(1, 10u32);
    h.push(2, 20u32);
    let peeked = h.peek().map(|(hh, p, v)| (hh, p, *v));
    assert_eq!(h.len(), 3, "peek must not consume");
    let popped = h.pop().map(|(hh, p, v)| (hh, p, v));
    assert_eq!(peeked, popped);
}

#[test]
fn decrease_key_moves_an_entry_ahead() {
    let mut h = HandleHeap::new();
    h.push(10, "a".to_string());
    h.push(20, "b".to_string());
    let hc = h.push(30, "c".to_string());
    h.push(40, "d".to_string());

    h.decrease_key(hc, 5).expect("live handle, lower priority");
    assert_eq!(h.priority_of(hc), Some(5));
    assert_eq!(
        drain(&mut h),
        vec![
            (5, "c".to_string()),
            (10, "a".to_string()),
            (20, "b".to_string()),
            (40, "d".to_string()),
        ]
    );
}

#[test]
fn decrease_key_into_a_tie_keeps_original_enqueue_rank() {
    // Scenario 1: the OLDER entry joins the tie -> it goes first.
    let mut h = HandleHeap::new();
    let ha = h.push(50, "old".to_string());
    h.push(30, "mid".to_string());
    h.push(50, "tail".to_string());
    h.decrease_key(ha, 30).unwrap();
    assert_eq!(
        drain(&mut h),
        vec![
            (30, "old".to_string()),
            (30, "mid".to_string()),
            (50, "tail".to_string()),
        ],
        "an entry enqueued earlier outranks a same-priority entry enqueued later"
    );

    // Scenario 2: the NEWER entry joins the tie -> it stays behind.
    let mut h = HandleHeap::new();
    h.push(30, "mid".to_string());
    let hc = h.push(50, "young".to_string());
    h.decrease_key(hc, 30).unwrap();
    assert_eq!(
        drain(&mut h),
        vec![(30, "mid".to_string()), (30, "young".to_string())]
    );
}

#[test]
fn decrease_key_to_current_priority_is_a_noop_ok() {
    let mut h = HandleHeap::new();
    h.push(7, "a");
    let hb = h.push(7, "b");
    assert_eq!(h.decrease_key(hb, 7), Ok(()));
    assert_eq!(h.priority_of(hb), Some(7));
    assert_eq!(drain(&mut h), vec![(7, "a"), (7, "b")], "noop must not disturb FIFO rank");
}

#[test]
fn decrease_key_refuses_to_increase_with_details() {
    let mut h = HandleHeap::new();
    h.push(1, "front");
    let hb = h.push(3, "target");
    h.push(9, "back");
    assert_eq!(
        h.decrease_key(hb, 7),
        Err(HeapError::WouldIncrease {
            current: 3,
            requested: 7,
        })
    );
    assert_eq!(h.priority_of(hb), Some(3), "rejected call must leave the heap untouched");
    assert_eq!(drain(&mut h), vec![(1, "front"), (3, "target"), (9, "back")]);
}

#[test]
fn popped_handle_is_stale_everywhere() {
    let mut h = HandleHeap::new();
    let ha = h.push(1, "gone");
    h.push(2, "stays");
    let (popped, _, _) = h.pop().unwrap();
    assert_eq!(popped, ha);

    assert!(!h.contains(ha));
    assert_eq!(h.priority_of(ha), None);
    assert_eq!(h.remove(ha), Err(HeapError::StaleHandle));
    // Staleness is checked before the increase rule:
    assert_eq!(h.decrease_key(ha, 999), Err(HeapError::StaleHandle));
    assert_eq!(h.decrease_key(ha, 0), Err(HeapError::StaleHandle));
    assert_eq!(h.len(), 1);
}

#[test]
fn removed_handle_is_stale_everywhere() {
    let mut h = HandleHeap::new();
    h.push(1, "keep");
    let hb = h.push(2, "cancel");
    assert_eq!(h.remove(hb), Ok((2, "cancel")));
    assert!(!h.contains(hb));
    assert_eq!(h.priority_of(hb), None);
    assert_eq!(h.remove(hb), Err(HeapError::StaleHandle));
    assert_eq!(h.decrease_key(hb, 0), Err(HeapError::StaleHandle));
    assert_eq!(h.len(), 1);
}

#[test]
fn remove_from_the_middle_preserves_dispatch_order() {
    // Push order chosen so a naive fix-up after the removal reorders wrongly
    // unless the displaced entry is allowed to move in BOTH directions.
    let mut h = HandleHeap::new();
    let mut target = None;
    for p in [0u64, 10, 1, 11, 12, 2, 4, 13, 14, 15, 16, 3] {
        let hh = h.push(p, p);
        if p == 11 {
            target = Some(hh);
        }
    }
    assert_eq!(h.remove(target.unwrap()), Ok((11, 11)));
    assert_eq!(h.len(), 11);
    let got: Vec<u64> = drain(&mut h).into_iter().map(|(p, _)| p).collect();
    assert_eq!(got, vec![0, 1, 2, 3, 4, 10, 12, 13, 14, 15, 16]);
}

#[test]
fn remove_head_and_remove_tail() {
    let mut h = HandleHeap::new();
    let ha = h.push(1, "head");
    h.push(2, "mid");
    let hc = h.push(3, "tail");
    assert_eq!(h.remove(ha), Ok((1, "head")));
    assert_eq!(h.peek().map(|(_, p, v)| (p, *v)), Some((2, "mid")));
    assert_eq!(h.remove(hc), Ok((3, "tail")));
    assert_eq!(drain(&mut h), vec![(2, "mid")]);
}

#[test]
fn handles_are_never_reused() {
    use std::collections::HashSet;
    let mut h = HandleHeap::new();
    let mut seen: HashSet<Handle> = HashSet::new();
    let mut live: Vec<Handle> = Vec::new();
    for round in 0..40u64 {
        let hh = h.push(round % 3, round);
        assert!(seen.insert(hh), "handle minted twice");
        live.push(hh);
        if round % 2 == 0 {
            h.pop();
        }
        if round % 5 == 4 {
            // cancel the newest still-live entry if any remain
            while let Some(cand) = live.pop() {
                if h.contains(cand) {
                    h.remove(cand).unwrap();
                    break;
                }
            }
        }
    }
    assert_eq!(seen.len(), 40);
}

#[test]
fn empty_heap_edges() {
    let mut h: HandleHeap<&str> = HandleHeap::new();
    assert_eq!(h.len(), 0);
    assert!(h.is_empty());
    assert_eq!(h.peek(), None);
    assert_eq!(h.pop(), None);

    let hh = h.push(1, "only");
    h.pop();
    assert!(h.is_empty());
    assert_eq!(h.peek(), None);
    assert!(!h.contains(hh));

    let hd: HandleHeap<String> = HandleHeap::default();
    assert!(hd.is_empty());
}

#[test]
fn len_tracks_every_mutation() {
    let mut h = HandleHeap::new();
    let a = h.push(4, ());
    assert_eq!(h.len(), 1);
    let b = h.push(2, ());
    assert_eq!(h.len(), 2);
    h.decrease_key(a, 1).unwrap();
    assert_eq!(h.len(), 2, "decrease_key must not change len");
    h.remove(b).unwrap();
    assert_eq!(h.len(), 1);
    h.pop().unwrap();
    assert_eq!(h.len(), 0);
}

/// Reference model: a flat Vec ordered by nothing, popped by scanning for
/// min (priority, enqueue_rank). Slow but obviously correct.
struct Model {
    live: Vec<(u64, u64, u64, Handle)>, // (priority, rank, value, handle)
    next_rank: u64,
}

impl Model {
    fn min_index(&self) -> Option<usize> {
        (0..self.live.len()).min_by_key(|&i| (self.live[i].0, self.live[i].1))
    }
}

#[test]
fn scripted_ops_match_reference_model() {
    let mut rng = Lcg(0xd15b_a7c4_0000_0001);
    let mut heap: HandleHeap<u64> = HandleHeap::new();
    let mut model = Model {
        live: Vec::new(),
        next_rank: 0,
    };
    let mut dead: Vec<Handle> = Vec::new();

    for step in 0..400 {
        match rng.below(10) {
            0..=3 => {
                let p = rng.below(24);
                let v = step as u64;
                let hh = heap.push(p, v);
                model.live.push((p, model.next_rank, v, hh));
                model.next_rank += 1;
            }
            4..=5 => {
                let expected = model.min_index().map(|i| {
                    let e = model.live.remove(i);
                    (e.3, e.0, e.2)
                });
                if let Some((eh, _, _)) = expected {
                    dead.push(eh);
                }
                assert_eq!(heap.pop(), expected, "step {step}: pop mismatch");
            }
            6 if !model.live.is_empty() => {
                let k = rng.below(model.live.len() as u64) as usize;
                let (p, _, _, hh) = model.live[k];
                let newp = rng.below(p + 1); // <= current
                assert_eq!(heap.decrease_key(hh, newp), Ok(()), "step {step}");
                model.live[k].0 = newp;
            }
            7 if !model.live.is_empty() => {
                let k = rng.below(model.live.len() as u64) as usize;
                let (p, _, v, hh) = model.live.remove(k);
                assert_eq!(heap.remove(hh), Ok((p, v)), "step {step}: remove mismatch");
                dead.push(hh);
            }
            8 if !model.live.is_empty() => {
                // an increase attempt must be rejected and change nothing
                let k = rng.below(model.live.len() as u64) as usize;
                let (p, _, _, hh) = model.live[k];
                let req = p + 1 + rng.below(5);
                assert_eq!(
                    heap.decrease_key(hh, req),
                    Err(HeapError::WouldIncrease {
                        current: p,
                        requested: req,
                    }),
                    "step {step}"
                );
            }
            9 if !dead.is_empty() => {
                let k = rng.below(dead.len() as u64) as usize;
                let hh = dead[k];
                assert!(!heap.contains(hh), "step {step}: dead handle resurrected");
                assert_eq!(heap.remove(hh), Err(HeapError::StaleHandle), "step {step}");
            }
            _ => {}
        }
        assert_eq!(heap.len(), model.live.len(), "step {step}: len drifted");
        let expected_peek = model
            .min_index()
            .map(|i| (model.live[i].3, model.live[i].0, model.live[i].2));
        assert_eq!(
            heap.peek().map(|(hh, p, v)| (hh, p, *v)),
            expected_peek,
            "step {step}: peek mismatch"
        );
    }

    // Drain what's left; the tail must still come out in exact model order.
    while let Some(i) = model.min_index() {
        let e = model.live.remove(i);
        assert_eq!(heap.pop(), Some((e.3, e.0, e.2)));
    }
    assert_eq!(heap.pop(), None);
}
