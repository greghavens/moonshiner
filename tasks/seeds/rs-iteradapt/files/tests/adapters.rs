// Acceptance tests for the in-house iterator adapters (itertools replacement).
//
// The laziness tests wrap the source in a Probe that counts every next() call
// the adapter makes on it. The pull schedules asserted here are the contract:
// construction pulls nothing, lookahead is bounded and buffered (never
// re-pulled), and once the source has returned None it is NEVER polled again.
//
// `intersperse` will collide with the unstable Iterator::intersperse when it
// stabilizes; we accept that knowingly, exactly like itertools users do.
#![allow(unstable_name_collisions)]

use std::cell::Cell;
use std::rc::Rc;

use rs_iteradapt::{ChunkBy, DedupByKey, Intersperse, IterAdapters};

/// Wraps an iterator and counts how many times next() is called on it.
struct Probe<I> {
    inner: I,
    pulls: Rc<Cell<usize>>,
}

fn probe<I: Iterator>(inner: I) -> (Probe<I>, Rc<Cell<usize>>) {
    let pulls = Rc::new(Cell::new(0));
    (
        Probe {
            inner,
            pulls: Rc::clone(&pulls),
        },
        pulls,
    )
}

impl<I: Iterator> Iterator for Probe<I> {
    type Item = I::Item;
    fn next(&mut self) -> Option<I::Item> {
        self.pulls.set(self.pulls.get() + 1);
        self.inner.next()
    }
}

// ---------------------------------------------------------------- chunk_by

#[test]
fn chunk_by_groups_consecutive_equal_runs() {
    let groups: Vec<Vec<i32>> = vec![1, 1, 2, 2, 2, 3, 1]
        .into_iter()
        .chunk_by(|a, b| a == b)
        .collect();
    assert_eq!(groups, vec![vec![1, 1], vec![2, 2, 2], vec![3], vec![1]]);
}

#[test]
fn chunk_by_predicate_sees_previous_element_then_next() {
    // descending runs: the pair is (immediately preceding element, candidate)
    let groups: Vec<Vec<i32>> = vec![3, 2, 5, 4, 1]
        .into_iter()
        .chunk_by(|prev, next| next < prev)
        .collect();
    assert_eq!(groups, vec![vec![3, 2], vec![5, 4, 1]]);
}

#[test]
fn chunk_by_edge_shapes() {
    let empty: Vec<Vec<i32>> = Vec::<i32>::new().into_iter().chunk_by(|a, b| a == b).collect();
    assert_eq!(empty, Vec::<Vec<i32>>::new());

    let single: Vec<Vec<i32>> = vec![9].into_iter().chunk_by(|a, b| a == b).collect();
    assert_eq!(single, vec![vec![9]]);

    let all_same: Vec<Vec<i32>> = vec![4, 4, 4].into_iter().chunk_by(|a, b| a == b).collect();
    assert_eq!(all_same, vec![vec![4, 4, 4]]);

    let never: Vec<Vec<i32>> = vec![1, 2, 3].into_iter().chunk_by(|_, _| false).collect();
    assert_eq!(never, vec![vec![1], vec![2], vec![3]]);
}

#[test]
fn chunk_by_pull_schedule() {
    let (src, pulls) = probe(vec![1, 1, 2].into_iter());
    let mut it = src.chunk_by(|a, b| a == b);
    assert_eq!(pulls.get(), 0, "construction must not consume the source");

    assert_eq!(it.next(), Some(vec![1, 1]));
    assert_eq!(pulls.get(), 3, "group + the boundary element that ends it");

    assert_eq!(it.next(), Some(vec![2]));
    assert_eq!(pulls.get(), 4, "buffered boundary element reused, then None");

    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 4, "source already returned None — never poll it again");
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 4);
}

#[test]
fn chunk_by_requires_no_clone() {
    #[derive(Debug, PartialEq)]
    struct Reading(i32); // deliberately NOT Clone

    let groups: Vec<Vec<Reading>> = vec![Reading(1), Reading(1), Reading(5)]
        .into_iter()
        .chunk_by(|a, b| a.0 == b.0)
        .collect();
    assert_eq!(
        groups,
        vec![vec![Reading(1), Reading(1)], vec![Reading(5)]]
    );
}

// ------------------------------------------------------------ dedup_by_key

#[test]
fn dedup_by_key_collapses_consecutive_runs_only() {
    let out: Vec<i32> = vec![1, 1, 2, 1].into_iter().dedup_by_key(|&x| x).collect();
    assert_eq!(out, vec![1, 2, 1], "non-adjacent repeats survive");

    let words: Vec<&str> = vec!["apple", "avocado", "banana", "blueberry", "cherry"]
        .into_iter()
        .dedup_by_key(|w| w.as_bytes()[0])
        .collect();
    assert_eq!(words, vec!["apple", "banana", "cherry"]);
}

#[test]
fn dedup_by_key_edge_shapes() {
    let empty: Vec<i32> = Vec::<i32>::new().into_iter().dedup_by_key(|&x| x).collect();
    assert_eq!(empty, Vec::<i32>::new());

    let all_same: Vec<i32> = vec![7, 7, 7, 7].into_iter().dedup_by_key(|&x| x).collect();
    assert_eq!(all_same, vec![7]);

    let distinct: Vec<i32> = vec![1, 2, 3].into_iter().dedup_by_key(|&x| x).collect();
    assert_eq!(distinct, vec![1, 2, 3]);
}

#[test]
fn dedup_by_key_keeps_the_first_of_each_run() {
    // key = tens digit; the first representative of each run must be yielded
    let out: Vec<i32> = vec![10, 11, 12, 25, 27, 12]
        .into_iter()
        .dedup_by_key(|&x| x / 10)
        .collect();
    assert_eq!(out, vec![10, 25, 12]);
}

#[test]
fn dedup_by_key_pull_schedule() {
    let (src, pulls) = probe(vec![1, 1, 1, 2].into_iter());
    let mut it = src.dedup_by_key(|&x| x);
    assert_eq!(pulls.get(), 0, "construction must not consume the source");

    assert_eq!(it.next(), Some(1));
    assert_eq!(pulls.get(), 1, "first item needs exactly one pull");

    assert_eq!(it.next(), Some(2));
    assert_eq!(pulls.get(), 4, "skipped the two duplicates, then the 2");

    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 5, "one pull to observe the end");
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 5, "exhausted source must not be polled again");
}

#[test]
fn dedup_by_key_requires_no_clone_on_items() {
    #[derive(Debug, PartialEq)]
    struct Event {
        shard: u8, // deliberately NOT Clone
    }
    let out: Vec<Event> = vec![Event { shard: 1 }, Event { shard: 1 }, Event { shard: 2 }]
        .into_iter()
        .dedup_by_key(|e| e.shard)
        .collect();
    assert_eq!(out, vec![Event { shard: 1 }, Event { shard: 2 }]);
}

// ------------------------------------------------------------- intersperse

#[test]
fn intersperse_places_separator_between_elements_only() {
    let out: Vec<i32> = vec![1, 2, 3].into_iter().intersperse(0).collect();
    assert_eq!(out, vec![1, 0, 2, 0, 3]);

    let empty: Vec<i32> = Vec::<i32>::new().into_iter().intersperse(0).collect();
    assert_eq!(empty, Vec::<i32>::new());

    let single: Vec<i32> = vec![42].into_iter().intersperse(0).collect();
    assert_eq!(single, vec![42]);
}

#[test]
fn intersperse_builds_joined_strings() {
    let csv: String = vec!["10.0.0.1", "10.0.0.2", "10.0.0.3"]
        .into_iter()
        .intersperse(", ")
        .collect();
    assert_eq!(csv, "10.0.0.1, 10.0.0.2, 10.0.0.3");
}

#[test]
fn intersperse_pull_schedule() {
    let (src, pulls) = probe(vec![10, 20].into_iter());
    let mut it = src.intersperse(0);
    assert_eq!(pulls.get(), 0, "construction must not consume the source");

    assert_eq!(it.next(), Some(10));
    assert_eq!(pulls.get(), 1, "first element: exactly one pull, no lookahead yet");

    assert_eq!(it.next(), Some(0));
    assert_eq!(pulls.get(), 2, "separator only after confirming a successor exists");

    assert_eq!(it.next(), Some(20));
    assert_eq!(pulls.get(), 2, "successor was buffered — no new pull");

    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 3, "one pull to observe the end");

    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 3, "exhausted source must not be polled again");
}

#[test]
fn intersperse_pull_schedule_empty_and_single() {
    let (src, pulls) = probe(Vec::<i32>::new().into_iter());
    let mut it = src.intersperse(0);
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 1);
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 1);

    let (src, pulls) = probe(vec![7].into_iter());
    let mut it = src.intersperse(0);
    assert_eq!(it.next(), Some(7));
    assert_eq!(pulls.get(), 1);
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 2);
    assert_eq!(it.next(), None);
    assert_eq!(pulls.get(), 2);
}

#[test]
fn intersperse_clones_the_separator_once_per_emission_and_never_clones_items() {
    #[derive(Debug)]
    struct Tick {
        id: u8,
        clones: Rc<Cell<usize>>,
    }
    impl Clone for Tick {
        fn clone(&self) -> Tick {
            self.clones.set(self.clones.get() + 1);
            Tick {
                id: self.id,
                clones: Rc::clone(&self.clones),
            }
        }
    }

    let clones = Rc::new(Cell::new(0));
    let mk = |id: u8| Tick {
        id,
        clones: Rc::clone(&clones),
    };

    let out: Vec<Tick> = vec![mk(1), mk(2), mk(3)]
        .into_iter()
        .intersperse(mk(0))
        .collect();
    let ids: Vec<u8> = out.iter().map(|t| t.id).collect();
    assert_eq!(ids, vec![1, 0, 2, 0, 3]);
    assert_eq!(
        clones.get(),
        2,
        "exactly one clone per emitted separator; items are moved, not cloned"
    );
}

// ------------------------------------------------------------- composition

#[test]
fn adapters_compose_like_any_iterator() {
    let out: Vec<i32> = vec![3, 3, 5, 5, 5, 2]
        .into_iter()
        .dedup_by_key(|&x| x)
        .intersperse(0)
        .collect();
    assert_eq!(out, vec![3, 0, 5, 0, 2]);

    let run_lengths: Vec<usize> = vec![1, 1, 2, 2, 2]
        .into_iter()
        .chunk_by(|a, b| a == b)
        .map(|group| group.len())
        .collect();
    assert_eq!(run_lengths, vec![2, 3]);
}

#[test]
fn adapter_structs_have_the_documented_shapes() {
    // These annotations fail to compile if the generic parameters drift.
    fn eq(a: &i32, b: &i32) -> bool {
        a == b
    }
    fn key(x: &i32) -> i32 {
        *x / 10
    }

    let cb: ChunkBy<std::vec::IntoIter<i32>, fn(&i32, &i32) -> bool> =
        vec![1, 1, 2].into_iter().chunk_by(eq as fn(&i32, &i32) -> bool);
    assert_eq!(cb.collect::<Vec<_>>(), vec![vec![1, 1], vec![2]]);

    let dd: DedupByKey<std::vec::IntoIter<i32>, i32, fn(&i32) -> i32> =
        vec![10, 11, 25].into_iter().dedup_by_key(key as fn(&i32) -> i32);
    assert_eq!(dd.collect::<Vec<_>>(), vec![10, 25]);

    let is: Intersperse<std::vec::IntoIter<i32>> = vec![1, 2].into_iter().intersperse(9);
    assert_eq!(is.collect::<Vec<_>>(), vec![1, 9, 2]);
}

#[test]
fn constructing_adapters_consumes_nothing_even_when_dropped() {
    let (src, pulls) = probe(vec![1, 2, 3].into_iter());
    let adapter = src.chunk_by(|a, b| a == b);
    drop(adapter);
    assert_eq!(pulls.get(), 0);

    let (src, pulls) = probe(vec![1, 2, 3].into_iter());
    let adapter = src.dedup_by_key(|&x| x);
    drop(adapter);
    assert_eq!(pulls.get(), 0);

    let (src, pulls) = probe(vec![1, 2, 3].into_iter());
    let adapter = src.intersperse(0);
    drop(adapter);
    assert_eq!(pulls.get(), 0);
}
