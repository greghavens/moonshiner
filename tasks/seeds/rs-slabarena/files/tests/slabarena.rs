//! Acceptance contract for the rs_slabarena generational arena.
//! Protected file: the implementation must satisfy these tests as written.

use rs_slabarena::{Arena, Handle};

/// Deterministic 64-bit LCG (MMIX constants). No environment seeding —
/// the scripted test below replays the exact same op sequence every run.
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

#[test]
fn insert_get_roundtrip_and_accounting() {
    let mut a: Arena<String> = Arena::new();
    assert!(a.is_empty());
    assert_eq!(a.len(), 0);
    assert_eq!(a.capacity(), 0);

    let h1 = a.insert("alpha".to_string());
    let h2 = a.insert("beta".to_string());
    let h3 = a.insert("gamma".to_string());

    assert!(!a.is_empty());
    assert_eq!(a.len(), 3);
    assert_eq!(a.capacity(), 3);
    assert_eq!(a.get(h1), Some(&"alpha".to_string()));
    assert_eq!(a.get(h2), Some(&"beta".to_string()));
    assert_eq!(a.get(h3), Some(&"gamma".to_string()));
    assert!(a.contains(h1) && a.contains(h2) && a.contains(h3));
}

#[test]
fn fresh_slots_number_upward_from_zero_at_generation_zero() {
    let mut a = Arena::new();
    let h0 = a.insert('a');
    let h1 = a.insert('b');
    let h2 = a.insert('c');
    assert_eq!((h0.index(), h0.generation()), (0, 0));
    assert_eq!((h1.index(), h1.generation()), (1, 0));
    assert_eq!((h2.index(), h2.generation()), (2, 0));
}

#[test]
fn get_mut_mutates_in_place() {
    let mut a = Arena::new();
    let h = a.insert(vec![1, 2]);
    a.get_mut(h).unwrap().push(3);
    assert_eq!(a.get(h), Some(&vec![1, 2, 3]));
}

#[test]
fn remove_returns_the_value_and_second_remove_misses() {
    let mut a = Arena::new();
    let h1 = a.insert("x".to_string());
    let h2 = a.insert("y".to_string());
    assert_eq!(a.remove(h1), Some("x".to_string()));
    assert_eq!(a.len(), 1);
    assert_eq!(a.capacity(), 2, "removing must not shrink the slot table");
    assert_eq!(a.remove(h1), None, "second remove of the same handle is a miss");
    assert_eq!(a.len(), 1);
    assert_eq!(a.get(h2), Some(&"y".to_string()));
}

#[test]
fn all_accessors_miss_on_a_stale_handle() {
    let mut a = Arena::new();
    let h = a.insert(7);
    assert!(a.contains(h));
    assert_eq!(a.remove(h), Some(7));
    assert!(!a.contains(h));
    assert_eq!(a.get(h), None);
    assert_eq!(a.get_mut(h), None);
    assert_eq!(a.remove(h), None);
}

#[test]
fn stale_handle_to_a_reused_slot_is_not_confused_with_the_new_tenant() {
    let mut a = Arena::new();
    let _keep = a.insert("keep".to_string());
    let old = a.insert("old".to_string());
    assert_eq!(a.remove(old), Some("old".to_string()));

    let new = a.insert("new".to_string());
    assert_eq!(new.index(), old.index(), "freed slot must be reused");
    assert_eq!(new.generation(), old.generation() + 1);

    assert_eq!(a.get(old), None, "old handle must not see the new tenant");
    assert!(!a.contains(old));
    assert_eq!(a.get_mut(old), None);
    assert_eq!(a.remove(old), None, "stale remove must not evict the new tenant");
    assert_eq!(a.get(new), Some(&"new".to_string()));
    assert_eq!(a.len(), 2);
}

#[test]
fn free_slots_are_reused_most_recently_freed_first() {
    let mut a = Arena::new();
    let h: Vec<Handle> = (0..5).map(|i| a.insert(i)).collect();
    assert_eq!(a.remove(h[1]), Some(1));
    assert_eq!(a.remove(h[3]), Some(3));

    let r1 = a.insert(103); // slot 3 was freed last -> comes back first
    let r2 = a.insert(101);
    let r3 = a.insert(200); // free list exhausted -> fresh slot
    assert_eq!((r1.index(), r1.generation()), (3, 1));
    assert_eq!((r2.index(), r2.generation()), (1, 1));
    assert_eq!((r3.index(), r3.generation()), (5, 0));
    assert_eq!(a.capacity(), 6);
    assert_eq!(a.len(), 6);
}

#[test]
fn iteration_walks_index_order_and_skips_tombstones() {
    let mut a = Arena::new();
    let h: Vec<Handle> = ["a", "b", "c", "d", "e"]
        .iter()
        .map(|s| a.insert(s.to_string()))
        .collect();
    a.remove(h[1]).unwrap();
    a.remove(h[3]).unwrap();

    let got: Vec<(u32, u32, String)> = a
        .iter()
        .map(|(hh, v)| (hh.index(), hh.generation(), v.clone()))
        .collect();
    assert_eq!(
        got,
        vec![
            (0, 0, "a".to_string()),
            (2, 0, "c".to_string()),
            (4, 0, "e".to_string()),
        ]
    );
}

#[test]
fn iteration_shows_reused_slots_in_index_position_with_new_generation() {
    let mut a = Arena::new();
    let h: Vec<Handle> = [10, 20, 30].iter().map(|v| a.insert(*v)).collect();
    a.remove(h[1]).unwrap();
    let hr = a.insert(99);
    assert_eq!(hr.index(), 1);

    let got: Vec<(u32, u32, i32)> = a
        .iter()
        .map(|(hh, v)| (hh.index(), hh.generation(), *v))
        .collect();
    assert_eq!(got, vec![(0, 0, 10), (1, 1, 99), (2, 0, 30)]);
}

#[test]
fn generation_strictly_increments_on_every_reuse() {
    let mut a = Arena::new();
    let mut handles = Vec::new();
    let mut h = a.insert(0u32);
    handles.push(h);
    for round in 1..=50u32 {
        assert_eq!(a.remove(h), Some(round - 1));
        h = a.insert(round);
        assert_eq!(h.index(), 0, "single-slot arena must keep reusing slot 0");
        assert_eq!(h.generation(), round);
        handles.push(h);
    }
    for (round, old) in handles.iter().enumerate().take(50) {
        assert_eq!(a.get(*old), None, "handle from round {round} must be stale");
    }
    assert_eq!(a.get(h), Some(&50));
    assert_eq!(a.capacity(), 1);
    assert_eq!(a.len(), 1);
}

#[test]
fn emptied_arena_iterates_nothing_but_keeps_its_slots() {
    let mut a = Arena::new();
    assert_eq!(a.iter().count(), 0);
    let hs: Vec<Handle> = (0..4).map(|i| a.insert(i)).collect();
    for h in hs {
        a.remove(h).unwrap();
    }
    assert_eq!(a.iter().count(), 0);
    assert_eq!(a.len(), 0);
    assert!(a.is_empty());
    assert_eq!(a.capacity(), 4, "slots are retained for reuse, never shrunk");
}

#[test]
fn default_is_an_empty_arena() {
    let a: Arena<u8> = Arena::default();
    assert!(a.is_empty());
    assert_eq!(a.capacity(), 0);
}

#[test]
fn handle_is_copy_eq_hash() {
    use std::collections::HashMap;
    let mut a = Arena::new();
    let h1 = a.insert(10);
    let h2 = a.insert(20);
    let mut m = HashMap::new();
    m.insert(h1, "first");
    m.insert(h2, "second");
    let h1_copy = h1; // Copy: h1 stays usable
    assert_eq!(h1, h1_copy);
    assert_ne!(h1, h2);
    assert_eq!(m.get(&h1), Some(&"first"));
    assert_eq!(m.get(&h2), Some(&"second"));
}

#[derive(Debug, PartialEq)]
struct Payload {
    tag: String, // deliberately neither Copy nor Clone nor Default
}

#[test]
fn works_with_non_clone_values() {
    let mut a = Arena::new();
    let h = a.insert(Payload { tag: "job-7".into() });
    assert_eq!(a.get(h).map(|p| p.tag.as_str()), Some("job-7"));
    let back = a.remove(h).unwrap();
    assert_eq!(back, Payload { tag: "job-7".into() });
}

#[test]
fn scripted_mixed_ops_match_the_contract_model() {
    let mut rng = Lcg(0x51ab_a12e_5eed_0001);
    let mut arena: Arena<u64> = Arena::new();

    // Mirror of the pinned contract: per-slot generation, LIFO free stack.
    let mut gens: Vec<u32> = Vec::new();
    let mut free: Vec<u32> = Vec::new();
    let mut live: Vec<(Handle, u64)> = Vec::new(); // insertion order
    let mut dead: Vec<Handle> = Vec::new();
    let mut counter = 0u64;

    for step in 0..600 {
        match rng.below(10) {
            0..=4 => {
                counter += 1;
                let expected_idx = match free.pop() {
                    Some(top) => top,
                    None => {
                        gens.push(0);
                        gens.len() as u32 - 1
                    }
                };
                let h = arena.insert(counter);
                assert_eq!(h.index(), expected_idx, "step {step}: wrong slot chosen");
                assert_eq!(
                    h.generation(),
                    gens[expected_idx as usize],
                    "step {step}: wrong generation"
                );
                live.push((h, counter));
            }
            5..=7 if !live.is_empty() => {
                let k = rng.below(live.len() as u64) as usize;
                let (h, v) = live.remove(k);
                assert_eq!(arena.remove(h), Some(v), "step {step}: remove lost a value");
                gens[h.index() as usize] += 1;
                free.push(h.index());
                dead.push(h);
            }
            8 if !dead.is_empty() => {
                let k = rng.below(dead.len() as u64) as usize;
                let h = dead[k];
                assert_eq!(arena.get(h), None, "step {step}: stale handle must miss");
                assert!(!arena.contains(h), "step {step}");
            }
            _ if !live.is_empty() => {
                let k = rng.below(live.len() as u64) as usize;
                let (h, v) = live[k];
                assert_eq!(arena.get(h), Some(&v), "step {step}: live handle must hit");
            }
            _ => {}
        }
        assert_eq!(arena.len(), live.len(), "step {step}: len out of sync");
        assert_eq!(arena.capacity(), gens.len(), "step {step}: capacity out of sync");
    }

    // Final sweep: iteration yields exactly the live set, ordered by slot index.
    let mut expected: Vec<(u32, u32, u64)> = live
        .iter()
        .map(|(h, v)| (h.index(), h.generation(), *v))
        .collect();
    expected.sort_by_key(|e| e.0);
    let got: Vec<(u32, u32, u64)> = arena
        .iter()
        .map(|(h, v)| (h.index(), h.generation(), *v))
        .collect();
    assert_eq!(got, expected);
}
