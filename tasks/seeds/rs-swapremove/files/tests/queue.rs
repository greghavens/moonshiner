use rs_swapremove::BuildQueue;

#[test]
fn enqueue_and_inspect() {
    let mut q = BuildQueue::new();
    assert!(q.is_empty());
    q.enqueue(7, "billing", "main");
    q.enqueue(3, "website", "main");
    q.enqueue(9, "billing", "release");
    assert_eq!(q.len(), 3);
    assert_eq!(q.queued_ids(), vec![3, 7, 9]);
    assert_eq!(q.ids_for_repo("billing"), vec![7, 9]);
}

#[test]
fn cancel_repo_with_no_builds_is_zero() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "website", "main");
    assert_eq!(q.cancel_repo("billing"), 0);
    assert_eq!(q.queued_ids(), vec![1]);
}

#[test]
fn cancel_repo_on_empty_queue() {
    let mut q = BuildQueue::new();
    assert_eq!(q.cancel_repo("billing"), 0);
    assert!(q.is_empty());
}

#[test]
fn cancel_single_build_at_front() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    q.enqueue(2, "website", "main");
    q.enqueue(3, "docs", "main");
    assert_eq!(q.cancel_repo("billing"), 1);
    assert_eq!(q.queued_ids(), vec![2, 3]);
}

#[test]
fn cancel_single_build_at_back() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "website", "main");
    q.enqueue(2, "docs", "main");
    q.enqueue(3, "billing", "main");
    assert_eq!(q.cancel_repo("billing"), 1);
    assert_eq!(q.queued_ids(), vec![1, 2]);
}

#[test]
fn archiving_clears_every_queued_build() {
    let mut q = BuildQueue::new();
    for id in 1..=4 {
        q.enqueue(id, "monorepo", "main");
    }
    assert_eq!(q.cancel_repo("monorepo"), 4);
    assert!(q.is_empty());
    assert_eq!(q.ids_for_repo("monorepo"), vec![]);
}

#[test]
fn matching_build_at_the_tail_is_cancelled_too() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "website", "main");
    q.enqueue(2, "billing", "main");
    q.enqueue(3, "docs", "main");
    q.enqueue(4, "billing", "release");
    assert_eq!(q.cancel_repo("billing"), 2);
    assert_eq!(q.queued_ids(), vec![1, 3]);
    assert_eq!(q.ids_for_repo("billing"), vec![]);
}

#[test]
fn interleaved_builds_all_cancelled() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    q.enqueue(2, "website", "main");
    q.enqueue(3, "billing", "pr-41");
    q.enqueue(4, "docs", "main");
    q.enqueue(5, "billing", "pr-42");
    assert_eq!(q.cancel_repo("billing"), 3);
    assert_eq!(q.queued_ids(), vec![2, 4]);
}

#[test]
fn busy_repo_block_in_the_middle() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "website", "main");
    q.enqueue(2, "monorepo", "pr-7");
    q.enqueue(3, "monorepo", "pr-8");
    q.enqueue(4, "monorepo", "pr-9");
    q.enqueue(5, "docs", "main");
    assert_eq!(q.cancel_repo("monorepo"), 3);
    assert_eq!(q.queued_ids(), vec![1, 5]);
}

#[test]
fn audit_count_matches_what_was_queued() {
    let mut q = BuildQueue::new();
    q.enqueue(10, "billing", "main");
    q.enqueue(11, "billing", "main");
    q.enqueue(12, "website", "main");
    q.enqueue(13, "billing", "pr-2");
    q.enqueue(14, "billing", "pr-3");
    q.enqueue(15, "billing", "pr-4");
    let queued_before = q.ids_for_repo("billing").len();
    assert_eq!(q.cancel_repo("billing"), queued_before);
    assert_eq!(q.ids_for_repo("billing"), vec![]);
    assert_eq!(q.queued_ids(), vec![12]);
}

#[test]
fn second_cancel_finds_nothing_left() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    q.enqueue(2, "billing", "pr-1");
    q.enqueue(3, "website", "main");
    q.enqueue(4, "billing", "pr-2");
    q.enqueue(5, "billing", "pr-3");
    q.cancel_repo("billing");
    // One pass must be enough: pressing cancel again is a no-op.
    assert_eq!(q.cancel_repo("billing"), 0);
    assert_eq!(q.queued_ids(), vec![3]);
}

#[test]
fn other_repos_are_untouched() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    q.enqueue(2, "website", "main");
    q.enqueue(3, "billing", "pr-1");
    q.enqueue(4, "billing", "pr-2");
    q.enqueue(5, "docs", "main");
    q.enqueue(6, "billing", "pr-3");
    q.cancel_repo("billing");
    assert_eq!(q.queued_ids(), vec![2, 5]);
    assert_eq!(q.ids_for_repo("website"), vec![2]);
    assert_eq!(q.ids_for_repo("docs"), vec![5]);
}

#[test]
fn force_push_drops_only_that_branch() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    q.enqueue(2, "billing", "pr-9");
    q.enqueue(3, "billing", "pr-9");
    q.enqueue(4, "website", "pr-9");
    assert_eq!(q.cancel_branch("billing", "pr-9"), 2);
    assert_eq!(q.queued_ids(), vec![1, 4]);
}

#[test]
fn force_push_with_nothing_queued_is_zero() {
    let mut q = BuildQueue::new();
    q.enqueue(1, "billing", "main");
    assert_eq!(q.cancel_branch("billing", "pr-1"), 0);
    assert_eq!(q.len(), 1);
}
