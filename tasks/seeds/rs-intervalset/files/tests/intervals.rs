// Acceptance tests for the byte-range tracker (IntervalSet).
// Intervals are half-open [start, end) over u64. The set must stay
// normalized at all times: sorted, non-overlapping, non-adjacent (touching
// spans merged), no empty spans.

use rs_intervalset::IntervalSet;

fn spans(set: &IntervalSet) -> Vec<(u64, u64)> {
    set.iter().collect()
}

/// The invariant every mutation must preserve.
fn assert_normalized(set: &IntervalSet) {
    let v = spans(set);
    for &(start, end) in &v {
        assert!(start < end, "empty or inverted span {start}..{end}");
    }
    for pair in v.windows(2) {
        assert!(
            pair[0].1 < pair[1].0,
            "spans {:?} and {:?} must be sorted, disjoint, and non-adjacent",
            pair[0],
            pair[1]
        );
    }
    assert_eq!(set.len(), v.len());
    assert_eq!(set.is_empty(), v.is_empty());
    assert_eq!(set.total(), v.iter().map(|&(s, e)| e - s).sum::<u64>());
}

#[test]
fn insert_into_empty_set() {
    let mut set = IntervalSet::new();
    assert!(set.is_empty());
    assert_eq!(set.len(), 0);
    assert_eq!(set.total(), 0);

    set.insert(5, 10);
    assert_eq!(spans(&set), vec![(5, 10)]);
    assert_eq!(set.total(), 5);
    assert_normalized(&set);
}

#[test]
fn contains_respects_half_open_bounds() {
    let mut set = IntervalSet::new();
    set.insert(5, 10);
    assert!(!set.contains(4));
    assert!(set.contains(5), "start is inclusive");
    assert!(set.contains(9));
    assert!(!set.contains(10), "end is exclusive");
}

#[test]
fn adjacent_spans_merge_but_gap_of_one_does_not() {
    let mut set = IntervalSet::new();
    set.insert(0, 5);
    set.insert(5, 10);
    assert_eq!(spans(&set), vec![(0, 10)], "[0,5) + [5,10) touch — merge");

    let mut apart = IntervalSet::new();
    apart.insert(0, 5);
    apart.insert(6, 10);
    assert_eq!(
        spans(&apart),
        vec![(0, 5), (6, 10)],
        "a gap of one point keeps spans separate"
    );
    assert_normalized(&apart);
}

#[test]
fn overlapping_inserts_merge() {
    let mut set = IntervalSet::new();
    set.insert(0, 5);
    set.insert(3, 8);
    assert_eq!(spans(&set), vec![(0, 8)]);

    // one insert bridging three existing spans (adjacency on both outer edges)
    let mut bridge = IntervalSet::new();
    bridge.insert(0, 2);
    bridge.insert(4, 6);
    bridge.insert(8, 10);
    bridge.insert(2, 8);
    assert_eq!(spans(&bridge), vec![(0, 10)]);
    assert_normalized(&bridge);
}

#[test]
fn subsumed_and_duplicate_inserts_change_nothing() {
    let mut set = IntervalSet::new();
    set.insert(0, 10);
    set.insert(2, 5);
    set.insert(0, 10);
    assert_eq!(spans(&set), vec![(0, 10)]);
    assert_eq!(set.total(), 10);
}

#[test]
fn degenerate_ranges_are_ignored() {
    let mut set = IntervalSet::new();
    set.insert(5, 5);
    set.insert(7, 3); // inverted — treat as empty
    assert!(set.is_empty());

    set.insert(0, 4);
    set.remove(2, 2);
    set.remove(9, 6);
    assert_eq!(spans(&set), vec![(0, 4)]);
}

#[test]
fn remove_splits_a_span_in_two() {
    let mut set = IntervalSet::new();
    set.insert(0, 10);
    set.remove(3, 6);
    assert_eq!(spans(&set), vec![(0, 3), (6, 10)]);
    assert_eq!(set.total(), 7);
    assert_normalized(&set);
}

#[test]
fn remove_trims_edges_and_deletes_exact_spans() {
    let mut set = IntervalSet::new();
    set.insert(0, 10);
    set.remove(0, 3);
    assert_eq!(spans(&set), vec![(3, 10)]);
    set.remove(8, 10);
    assert_eq!(spans(&set), vec![(3, 8)]);
    set.remove(3, 8);
    assert!(set.is_empty());
    assert_normalized(&set);
}

#[test]
fn remove_spanning_multiple_spans() {
    let mut set = IntervalSet::new();
    set.insert(0, 5);
    set.insert(10, 15);
    set.insert(20, 25);
    set.remove(3, 22);
    assert_eq!(spans(&set), vec![(0, 3), (22, 25)]);
    assert_normalized(&set);
}

#[test]
fn remove_outside_coverage_is_a_noop() {
    let mut set = IntervalSet::new();
    set.insert(10, 20);
    set.remove(0, 10);
    set.remove(20, 30);
    assert_eq!(spans(&set), vec![(10, 20)]);
}

#[test]
fn covers_requires_the_whole_query_range() {
    let mut set = IntervalSet::new();
    set.insert(0, 5);
    set.insert(6, 10);
    assert!(set.covers(0, 5));
    assert!(set.covers(6, 10));
    assert!(set.covers(7, 9));
    assert!(!set.covers(0, 6), "the point 5 is uncovered");
    assert!(!set.covers(4, 7));
    assert!(!set.covers(5, 6));
    assert!(set.covers(3, 3), "empty query is vacuously covered");
    assert!(set.covers(5, 5), "even inside a gap");

    let empty = IntervalSet::new();
    assert!(!empty.covers(0, 1));
    assert!(empty.covers(3, 3));
}

#[test]
fn gaps_lists_uncovered_subranges_clipped_to_the_window() {
    let mut set = IntervalSet::new();
    set.insert(2, 4);
    set.insert(6, 9);
    set.insert(12, 14);

    assert_eq!(
        set.gaps(0, 16),
        vec![(0, 2), (4, 6), (9, 12), (14, 16)],
        "window wider than coverage"
    );
    assert_eq!(set.gaps(3, 13), vec![(4, 6), (9, 12)], "window clips both ends");
    assert_eq!(set.gaps(2, 14), vec![(4, 6), (9, 12)]);
    assert_eq!(set.gaps(6, 9), vec![], "fully covered window");
    assert_eq!(set.gaps(4, 6), vec![(4, 6)], "window entirely inside a gap");
    assert_eq!(set.gaps(13, 20), vec![(14, 20)]);
    assert_eq!(set.gaps(5, 5), vec![], "empty window");

    let empty = IntervalSet::new();
    assert_eq!(empty.gaps(1, 5), vec![(1, 5)]);
}

#[test]
fn iterator_is_sorted_disjoint_and_restartable() {
    let mut set = IntervalSet::new();
    set.insert(30, 40);
    set.insert(0, 5);
    set.insert(10, 20);
    assert_eq!(spans(&set), vec![(0, 5), (10, 20), (30, 40)]);
    // iter() borrows — a second pass sees the same thing
    assert_eq!(set.iter().count(), 3);
    assert_eq!(set.iter().next(), Some((0, 5)));
}

#[test]
fn scripted_sequence_keeps_the_set_normalized() {
    let mut set = IntervalSet::new();
    let script: &[(&str, u64, u64)] = &[
        ("insert", 10, 20),
        ("insert", 30, 40),
        ("insert", 20, 25),
        ("insert", 24, 32),
        ("remove", 15, 18),
        ("insert", 0, 5),
        ("remove", 12, 30),
        ("insert", 5, 10),
        ("remove", 0, 1),
        ("remove", 39, 45),
        ("insert", 12, 12),
        ("remove", 50, 60),
        ("insert", 29, 30),
        ("remove", 5, 5),
    ];
    for &(op, start, end) in script {
        match op {
            "insert" => set.insert(start, end),
            "remove" => set.remove(start, end),
            _ => unreachable!(),
        }
        assert_normalized(&set);
    }
    assert_eq!(spans(&set), vec![(1, 12), (29, 39)]);
    assert_eq!(set.total(), 21);
    assert_eq!(set.len(), 2);
}

#[test]
fn checkpoints_of_the_scripted_sequence_are_exact() {
    let mut set = IntervalSet::new();
    set.insert(10, 20);
    set.insert(30, 40);
    set.insert(20, 25);
    assert_eq!(spans(&set), vec![(10, 25), (30, 40)]);
    set.insert(24, 32);
    assert_eq!(spans(&set), vec![(10, 40)]);
    set.remove(15, 18);
    assert_eq!(spans(&set), vec![(10, 15), (18, 40)]);
    set.insert(0, 5);
    set.remove(12, 30);
    assert_eq!(spans(&set), vec![(0, 5), (10, 12), (30, 40)]);
    set.insert(5, 10);
    assert_eq!(spans(&set), vec![(0, 12), (30, 40)], "insert welds both neighbors");
}

#[test]
fn u64_extremes_are_usable() {
    let mut set = IntervalSet::new();
    set.insert(u64::MAX - 3, u64::MAX);
    assert!(set.contains(u64::MAX - 1));
    assert!(!set.contains(u64::MAX));
    assert_eq!(set.total(), 3);

    set.insert(0, u64::MAX);
    assert_eq!(spans(&set), vec![(0, u64::MAX)]);
    assert_eq!(set.total(), u64::MAX);
    assert_normalized(&set);

    let mut high = IntervalSet::new();
    high.insert(u64::MAX - 10, u64::MAX - 5);
    assert_eq!(
        high.gaps(u64::MAX - 12, u64::MAX),
        vec![(u64::MAX - 12, u64::MAX - 10), (u64::MAX - 5, u64::MAX)]
    );
}
