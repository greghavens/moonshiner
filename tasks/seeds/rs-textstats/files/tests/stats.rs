//! Regression suite for the shipped streaming counters. Everything in this
//! file passes today and must keep passing.

use rs_textstats::{Summary, TextStats};

fn stats_of(chunks: &[&str]) -> Summary {
    let mut ts = TextStats::new();
    for c in chunks {
        ts.feed(c);
    }
    ts.summary()
}

#[test]
fn empty_input_is_all_zeroes() {
    assert_eq!(
        stats_of(&[]),
        Summary {
            lines: 0,
            words: 0,
            chars: 0,
            bytes: 0,
            max_line_len: 0,
            min_line_len: 0,
        }
    );
}

#[test]
fn single_line_without_newline() {
    let s = stats_of(&["hello world"]);
    assert_eq!(s.lines, 1);
    assert_eq!(s.words, 2);
    assert_eq!(s.chars, 11);
    assert_eq!(s.bytes, 11);
    assert_eq!(s.max_line_len, 11);
    assert_eq!(s.min_line_len, 11);
}

#[test]
fn trailing_newline_opens_no_phantom_line() {
    let s = stats_of(&["one\ntwo\n"]);
    assert_eq!(s.lines, 2);
    assert_eq!(s.max_line_len, 3);
    assert_eq!(s.min_line_len, 3);
}

#[test]
fn unterminated_final_line_still_counts() {
    let s = stats_of(&["one\ntwo"]);
    assert_eq!(s.lines, 2);
    assert_eq!(s.words, 2);
}

#[test]
fn word_split_across_feeds_counts_once() {
    let s = stats_of(&["hel", "lo world"]);
    assert_eq!(s.words, 2);
    assert_eq!(s.chars, 11);
}

#[test]
fn whitespace_at_feed_boundary_separates_words() {
    let s = stats_of(&["a ", " b"]);
    assert_eq!(s.words, 2);
}

#[test]
fn line_split_across_feeds_counts_once() {
    let s = stats_of(&["ab", "cd\nef"]);
    assert_eq!(s.lines, 2);
    assert_eq!(s.max_line_len, 4);
    assert_eq!(s.min_line_len, 2);
}

#[test]
fn unicode_chars_versus_bytes() {
    let s = stats_of(&["héllo 世界"]);
    assert_eq!(s.chars, 8);
    assert_eq!(s.bytes, 13);
    assert_eq!(s.words, 2);
    assert_eq!(s.max_line_len, 8);
}

#[test]
fn blank_line_in_the_middle_counts() {
    let s = stats_of(&["a\n\nb"]);
    assert_eq!(s.lines, 3);
    assert_eq!(s.min_line_len, 0);
    assert_eq!(s.max_line_len, 1);
}

#[test]
fn tabs_and_runs_of_spaces_separate_words() {
    let s = stats_of(&["a\t\tb   c"]);
    assert_eq!(s.words, 3);
}

#[test]
fn max_and_min_line_lengths() {
    let s = stats_of(&["abc\nz\nqqqqq\n"]);
    assert_eq!(s.max_line_len, 5);
    assert_eq!(s.min_line_len, 1);
}

#[test]
fn open_final_line_participates_in_extremes() {
    let s = stats_of(&["aaaa\nb"]);
    assert_eq!(s.min_line_len, 1);
    assert_eq!(s.max_line_len, 4);
}

#[test]
fn feeding_an_empty_chunk_changes_nothing() {
    let mut ts = TextStats::new();
    ts.feed("abc def");
    let before = ts.summary();
    ts.feed("");
    assert_eq!(ts.summary(), before);
}

#[test]
fn carriage_return_is_an_ordinary_whitespace_char() {
    let s = stats_of(&["a\r\nb"]);
    assert_eq!(s.lines, 2);
    assert_eq!(s.chars, 4);
    assert_eq!(s.words, 2);
    assert_eq!(s.max_line_len, 2); // "a\r" is two chars long
    assert_eq!(s.min_line_len, 1);
}

#[test]
fn one_char_feeds_equal_one_big_feed() {
    let text = "to be or not\nto be";
    let mut per_char = TextStats::new();
    for ch in text.chars() {
        per_char.feed(&ch.to_string());
    }
    assert_eq!(per_char.summary(), stats_of(&[text]));
}

#[test]
fn summary_does_not_consume_the_accumulator() {
    let mut ts = TextStats::new();
    ts.feed("alpha beta");
    assert_eq!(ts.summary(), ts.summary());
    ts.feed(" gamma");
    assert_eq!(ts.summary().words, 3);
}
