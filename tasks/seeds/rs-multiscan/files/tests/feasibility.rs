//! Production-scale feasibility gate. The corpus below is the size and shape
//! of one real redaction run (one document batch against one tenant rule
//! set), generated deterministically so every run measures the same work.
//!
//! The scan must produce exactly the pinned match set AND come in under the
//! wall-clock budget. Correctness first, so a fast-but-wrong scan cannot
//! pass; then the budget, so a correct-but-quadratic scan cannot either.

use std::time::{Duration, Instant};

use rs_multiscan::{Match, PatternSet};

const CORPUS_LEN: usize = 1_500_000;
const PATTERN_COUNT: usize = 1_200;
const PLANT_EVERY: usize = 4_096;
const SEED: u64 = 0x5eed_cafe_f00d_0001;

const BUDGET_MS: u64 = 1_500;
const EXPECTED_COUNT: usize = 369;
const EXPECTED_CHECKSUM: u64 = 0x21c8_7e42_d672_e0bd;

fn lcg_next(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *state >> 33
}

const ALPHABET: &[u8] = b"abcdefgh. ";

fn build_corpus() -> (String, Vec<String>) {
    let mut s = SEED;
    let mut patterns = Vec::with_capacity(PATTERN_COUNT);
    for _ in 0..PATTERN_COUNT {
        let plen = 8 + (lcg_next(&mut s) as usize % 9); // 8..=16 bytes
        let mut p = String::with_capacity(plen);
        for _ in 0..plen {
            p.push(ALPHABET[lcg_next(&mut s) as usize % ALPHABET.len()] as char);
        }
        patterns.push(p);
    }
    let mut text: Vec<u8> = Vec::with_capacity(CORPUS_LEN);
    for _ in 0..CORPUS_LEN {
        text.push(ALPHABET[lcg_next(&mut s) as usize % ALPHABET.len()]);
    }
    // Plant real occurrences at deterministic offsets so the match set is
    // non-trivial.
    let mut offset = 64;
    while offset + 16 < CORPUS_LEN {
        let pid = lcg_next(&mut s) as usize % patterns.len();
        let bytes = patterns[pid].as_bytes();
        text[offset..offset + bytes.len()].copy_from_slice(bytes);
        offset += PLANT_EVERY;
    }
    (String::from_utf8(text).unwrap(), patterns)
}

fn fnv1a_of_matches(matches: &[Match]) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325;
    for m in matches {
        h ^= m.start as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
        h ^= m.pattern as u64;
        h = h.wrapping_mul(0x0000_0100_0000_01b3);
    }
    h
}

#[test]
fn production_rule_set_scan_is_correct_and_fast_enough() {
    let (text, patterns) = build_corpus();
    let refs: Vec<&str> = patterns.iter().map(|p| p.as_str()).collect();
    let set = PatternSet::new(&refs);

    let started = Instant::now();
    let matches = set.find_all(&text);
    let elapsed = started.elapsed();
    eprintln!(
        "feasibility: {} matches in {:?} (count/checksum {} / {:#018x})",
        matches.len(),
        elapsed,
        matches.len(),
        fnv1a_of_matches(&matches)
    );

    // Exact match set first: a fast scan that misses overlaps, mis-ids
    // patterns or reorders output must fail here.
    assert!(
        matches.windows(2).all(|w| w[0] <= w[1]),
        "matches must be sorted by (start, pattern)"
    );
    assert_eq!(matches.len(), EXPECTED_COUNT, "wrong number of matches");
    assert_eq!(
        fnv1a_of_matches(&matches),
        EXPECTED_CHECKSUM,
        "match set diverges from the pinned reference"
    );

    // Then the operational budget.
    assert!(
        elapsed <= Duration::from_millis(BUDGET_MS),
        "scan took {elapsed:?}, budget is {BUDGET_MS}ms"
    );
}
