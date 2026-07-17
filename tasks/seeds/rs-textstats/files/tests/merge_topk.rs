//! Acceptance tests for mergeable partial states and top-K frequent words.
//! This is the contract for the new feature; tests/stats.rs must keep
//! passing unchanged alongside it.

use rs_textstats::TextStats;

fn fed(chunks: &[&str]) -> TextStats {
    let mut ts = TextStats::new();
    for c in chunks {
        ts.feed(c);
    }
    ts
}

fn words(pairs: &[(&str, usize)]) -> Vec<(String, usize)> {
    pairs.iter().map(|(w, n)| (w.to_string(), *n)).collect()
}

#[test]
fn merge_two_halves_equals_the_whole() {
    // The boundary splits the word "word" in half.
    let left = fed(&["streaming wor"]);
    let right = fed(&["d counts\nsecond line"]);
    let merged = left.merge(right);
    let whole = fed(&["streaming word counts\nsecond line"]);
    assert_eq!(merged.summary(), whole.summary());
}

#[test]
fn merge_is_associative_and_matches_the_whole() {
    let (a, b, c) = ("alpha bet", "a gamma\ndel", "ta epsilon");
    let whole_text = format!("{a}{b}{c}");
    let whole = fed(&[whole_text.as_str()]);

    let left_first = fed(&[a]).merge(fed(&[b])).merge(fed(&[c]));
    let right_first = fed(&[a]).merge(fed(&[b]).merge(fed(&[c])));

    assert_eq!(left_first.summary(), whole.summary());
    assert_eq!(right_first.summary(), whole.summary());
    assert_eq!(left_first.top_words(usize::MAX), whole.top_words(usize::MAX));
    assert_eq!(right_first.top_words(usize::MAX), whole.top_words(usize::MAX));
}

#[test]
fn merge_with_empty_left_is_identity() {
    let merged = TextStats::new().merge(fed(&["two words\n"]));
    assert_eq!(merged.summary(), fed(&["two words\n"]).summary());
}

#[test]
fn merge_with_empty_right_is_identity() {
    let merged = fed(&["two words\n"]).merge(TextStats::new());
    assert_eq!(merged.summary(), fed(&["two words\n"]).summary());
}

#[test]
fn merge_of_two_empties_is_empty() {
    let merged = TextStats::new().merge(TextStats::new());
    let s = merged.summary();
    assert_eq!((s.lines, s.words, s.chars, s.bytes), (0, 0, 0, 0));
}

#[test]
fn line_extremes_come_from_glued_lines_not_partial_ones() {
    // Concatenation is "aaaa\nbb\ncc": the two 1-char fragments of "bb" glue
    // into one 2-char line, so the minimum is 2, not 1.
    let merged = fed(&["aaaa\nb"]).merge(fed(&["b\ncc"]));
    let s = merged.summary();
    assert_eq!(s.lines, 3);
    assert_eq!(s.max_line_len, 4);
    assert_eq!(s.min_line_len, 2);
}

#[test]
fn word_glued_at_the_merge_boundary_counts_once() {
    let merged = fed(&["ban"]).merge(fed(&["ana split"]));
    assert_eq!(merged.summary().words, 2);
    assert_eq!(merged.top_words(1), words(&[("banana", 1)]));
}

#[test]
fn feeding_continues_after_a_merge() {
    let mut merged = fed(&["hello wo"]).merge(fed(&["rld"]));
    merged.feed("\nbye");
    let s = merged.summary();
    assert_eq!(s.lines, 2);
    assert_eq!(s.words, 3);
    assert_eq!(s.chars, 15);
    assert_eq!(s.max_line_len, 11);
    assert_eq!(s.min_line_len, 3);
}

#[test]
fn folding_chunks_matches_a_single_feed() {
    let text = "the quick brown fox\njumps over the lazy dog\nthe quick fox again";
    let pieces = [&text[..10], &text[10..23], &text[23..24], &text[24..]];

    let whole = fed(&[text]);
    let fold_left = pieces
        .iter()
        .map(|p| fed(&[*p]))
        .fold(TextStats::new(), TextStats::merge);
    let fold_right = pieces
        .iter()
        .rev()
        .map(|p| fed(&[*p]))
        .fold(TextStats::new(), |acc, part| part.merge(acc));

    assert_eq!(fold_left.summary(), whole.summary());
    assert_eq!(fold_right.summary(), whole.summary());
    assert_eq!(fold_left.top_words(usize::MAX), whole.top_words(usize::MAX));
    assert_eq!(fold_right.top_words(usize::MAX), whole.top_words(usize::MAX));
}

#[test]
fn top_words_orders_by_count_descending() {
    let ts = fed(&["the cat and the dog and the bird"]);
    assert_eq!(
        ts.top_words(3),
        words(&[("the", 3), ("and", 2), ("bird", 1)])
    );
}

#[test]
fn top_words_breaks_count_ties_lexicographically() {
    let ts = fed(&["pear apple pear apple plum"]);
    assert_eq!(ts.top_words(2), words(&[("apple", 2), ("pear", 2)]));
    assert_eq!(
        ts.top_words(5),
        words(&[("apple", 2), ("pear", 2), ("plum", 1)])
    );
}

#[test]
fn top_words_with_k_zero_is_empty() {
    let ts = fed(&["some words here"]);
    assert_eq!(ts.top_words(0), Vec::<(String, usize)>::new());
}

#[test]
fn top_words_with_k_beyond_distinct_returns_all() {
    let ts = fed(&["b a b"]);
    assert_eq!(ts.top_words(10), words(&[("b", 2), ("a", 1)]));
}

#[test]
fn top_words_is_case_sensitive_with_byte_order_ties() {
    let ts = fed(&["Log log LOG log"]);
    assert_eq!(
        ts.top_words(3),
        words(&[("log", 2), ("LOG", 1), ("Log", 1)])
    );
}

#[test]
fn boundary_word_lands_in_the_frequency_table() {
    let merged = fed(&["over"]).merge(fed(&["flow overflow"]));
    assert_eq!(merged.top_words(1), words(&[("overflow", 2)]));
}

#[test]
fn word_count_equals_the_sum_of_frequencies() {
    let ts = fed(&["to be or not to be\nthat is the question "]);
    let total: usize = ts.top_words(usize::MAX).iter().map(|(_, n)| n).sum();
    assert_eq!(ts.summary().words, total);
}

#[test]
fn tokens_are_verbatim_including_punctuation() {
    let ts = fed(&["err! err! ok"]);
    assert_eq!(ts.top_words(2), words(&[("err!", 2), ("ok", 1)]));
}

#[test]
fn top_words_is_deterministic_with_many_distinct_words() {
    let vocabulary: Vec<String> = (b'a'..=b'z').map(|c| format!("k{}", c as char)).collect();
    let text = vocabulary.join(" ");
    let expected: Vec<(String, usize)> = vocabulary.iter().map(|w| (w.clone(), 1)).collect();
    // 26 distinct words, all tied at count 1: the full lexicographic order is
    // pinned, twice, on independently built accumulators.
    assert_eq!(fed(&[text.as_str()]).top_words(26), expected);
    assert_eq!(fed(&[text.as_str()]).top_words(26), expected);
}

#[test]
fn unicode_word_glued_across_a_merge() {
    let merged = fed(&["naï"]).merge(fed(&["ve café"]));
    assert_eq!(merged.summary().words, 2);
    assert_eq!(
        merged.top_words(2),
        words(&[("café", 1), ("naïve", 1)])
    );
}
