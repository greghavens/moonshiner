//! Correctness contract for the scanner: exact match sets, ids, offsets and
//! ordering. These hold for any implementation, fast or slow.

use rs_multiscan::{Match, PatternSet};

fn hits(patterns: &[&str], text: &str) -> Vec<(usize, usize)> {
    PatternSet::new(patterns)
        .find_all(text)
        .into_iter()
        .map(|m| (m.start, m.pattern))
        .collect()
}

#[test]
fn finds_every_occurrence_of_a_single_pattern() {
    assert_eq!(hits(&["ab"], "abxaby ab"), vec![(0, 0), (3, 0), (7, 0)]);
}

#[test]
fn overlapping_occurrences_all_count() {
    assert_eq!(hits(&["aaa"], "aaaaa"), vec![(0, 0), (1, 0), (2, 0)]);
}

#[test]
fn a_pattern_that_prefixes_another_still_fires() {
    assert_eq!(hits(&["ab", "abc"], "zabcz"), vec![(1, 0), (1, 1)]);
}

#[test]
fn matches_are_ordered_by_start_then_pattern_id() {
    // "a" (id 2) and "abc" (id 1) both start at 0; "bc" (id 0) starts at 1.
    assert_eq!(hits(&["bc", "abc", "a"], "abc"), vec![(0, 1), (0, 2), (1, 0)]);
}

#[test]
fn pattern_ids_are_input_indexes() {
    assert_eq!(hits(&["xy", "q"], "qxy"), vec![(0, 1), (1, 0)]);
}

#[test]
fn duplicate_patterns_keep_their_own_ids() {
    assert_eq!(hits(&["ab", "ab"], "zab"), vec![(1, 0), (1, 1)]);
}

#[test]
fn no_hits_gives_an_empty_vec() {
    assert_eq!(hits(&["zz"], "abcabc"), Vec::<(usize, usize)>::new());
}

#[test]
fn empty_text_gives_no_hits() {
    assert_eq!(hits(&["a"], ""), Vec::<(usize, usize)>::new());
}

#[test]
fn empty_pattern_set_finds_nothing() {
    let set = PatternSet::new(&[]);
    assert!(set.is_empty());
    assert_eq!(set.find_all("anything at all"), Vec::<Match>::new());
}

#[test]
fn pattern_longer_than_text_cannot_match() {
    assert_eq!(hits(&["abcdef"], "abc"), Vec::<(usize, usize)>::new());
}

#[test]
fn matches_at_the_very_start_and_very_end() {
    assert_eq!(hits(&["ab"], "abab"), vec![(0, 0), (2, 0)]);
}

#[test]
fn one_byte_patterns_work() {
    assert_eq!(hits(&["a"], "banana"), vec![(1, 0), (3, 0), (5, 0)]);
}

#[test]
fn offsets_are_bytes_not_chars() {
    // "café résumé": é occupies two bytes (0xC3 0xA9).
    assert_eq!(
        hits(&["é", "su"], "café résumé"),
        vec![(3, 0), (7, 0), (9, 1), (12, 0)]
    );
}

#[test]
#[should_panic(expected = "pattern must be non-empty")]
fn empty_patterns_are_rejected() {
    let _ = PatternSet::new(&["ok", ""]);
}

#[test]
fn set_len_reports_pattern_count() {
    assert_eq!(PatternSet::new(&["a", "b", "c"]).len(), 3);
}

// ---------------------------------------------------------------------------
// Mid-size deterministic corpus: pins the full match set via count + an
// order-sensitive checksum, so a rewrite cannot drop, duplicate or reorder
// hits at a scale where hand-listing is impractical.
// ---------------------------------------------------------------------------

fn lcg_next(state: &mut u64) -> u64 {
    *state = state
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *state >> 33
}

const ALPHABET: &[u8] = b"abcdefgh. ";

fn build_corpus(
    len: usize,
    pattern_count: usize,
    plant_every: usize,
    seed: u64,
) -> (String, Vec<String>) {
    let mut s = seed;
    let mut patterns = Vec::with_capacity(pattern_count);
    for _ in 0..pattern_count {
        let plen = 8 + (lcg_next(&mut s) as usize % 9); // 8..=16 bytes
        let mut p = String::with_capacity(plen);
        for _ in 0..plen {
            p.push(ALPHABET[lcg_next(&mut s) as usize % ALPHABET.len()] as char);
        }
        patterns.push(p);
    }
    let mut text: Vec<u8> = Vec::with_capacity(len);
    for _ in 0..len {
        text.push(ALPHABET[lcg_next(&mut s) as usize % ALPHABET.len()]);
    }
    // Overwrite deterministic offsets with real occurrences so the corpus
    // actually contains hits.
    let mut offset = 64;
    while offset + 16 < len {
        let pid = lcg_next(&mut s) as usize % patterns.len();
        let bytes = patterns[pid].as_bytes();
        text[offset..offset + bytes.len()].copy_from_slice(bytes);
        offset += plant_every;
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
fn mid_size_corpus_match_set_is_pinned() {
    let (text, patterns) = build_corpus(50_000, 20, 1_500, 0xc0ff_ee15_600d_f00d);
    let refs: Vec<&str> = patterns.iter().map(|p| p.as_str()).collect();
    let matches = PatternSet::new(&refs).find_all(&text);
    assert!(
        matches.windows(2).all(|w| w[0] <= w[1]),
        "matches must be sorted by (start, pattern)"
    );
    assert_eq!(matches.len(), MID_EXPECTED_COUNT);
    assert_eq!(fnv1a_of_matches(&matches), MID_EXPECTED_CHECKSUM);
}

const MID_EXPECTED_COUNT: usize = 34;
const MID_EXPECTED_CHECKSUM: u64 = 0x167c_445b_a1b0_eb49;
