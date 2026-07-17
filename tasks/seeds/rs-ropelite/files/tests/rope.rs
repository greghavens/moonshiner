// Acceptance tests for the rope-lite editor buffer.
// ALL indices in the public API are CHAR indices (Unicode scalar values),
// never bytes. The differential test drives the buffer against a Vec<char>
// model through a fixed op script — no randomness.

use rs_ropelite::{EditError, TextBuffer, DEFAULT_CHUNK_CAPACITY};

/// The structural invariant: chunks are never empty, never over capacity
/// (in CHARS), and concatenate to the content. An empty buffer has no chunks.
fn assert_invariants(buf: &TextBuffer, expected: &str) {
    assert_eq!(buf.to_string(), expected);
    assert_eq!(buf.char_len(), expected.chars().count());
    assert_eq!(buf.is_empty(), expected.is_empty());
    let chunks: Vec<&str> = buf.chunks().collect();
    assert_eq!(chunks.len(), buf.chunk_count());
    let cap = buf.chunk_capacity();
    for chunk in &chunks {
        let n = chunk.chars().count();
        assert!(n >= 1, "empty chunk in {chunks:?}");
        assert!(n <= cap, "chunk holds {n} chars, capacity is {cap}");
    }
    assert_eq!(chunks.concat(), expected);
}

fn model_string(model: &[char]) -> String {
    model.iter().collect()
}

#[test]
fn empty_buffer_shape() {
    let buf = TextBuffer::new();
    assert_eq!(buf.char_len(), 0);
    assert!(buf.is_empty());
    assert_eq!(buf.to_string(), "");
    assert_eq!(buf.chunk_count(), 0);
    assert_eq!(buf.slice(0, 0), Ok(String::new()));
    assert_eq!(buf.chunk_capacity(), DEFAULT_CHUNK_CAPACITY);
    assert_eq!(DEFAULT_CHUNK_CAPACITY, 32);

    let default_buf = TextBuffer::default();
    assert!(default_buf.is_empty());
}

#[test]
fn ascii_insert_delete_slice() {
    let mut buf = TextBuffer::from_text("hello world");
    assert_eq!(buf.char_len(), 11);
    assert_eq!(buf.slice(0, 5), Ok("hello".to_string()));
    assert_eq!(buf.slice(6, 11), Ok("world".to_string()));

    buf.insert(5, ",").unwrap();
    assert_eq!(buf.to_string(), "hello, world");

    buf.delete(0, 7).unwrap();
    assert_eq!(buf.to_string(), "world");
    assert_invariants(&buf, "world");
}

#[test]
fn multibyte_ops_use_char_indices_not_bytes() {
    let mut buf = TextBuffer::with_chunk_capacity(4);
    buf.insert(0, "日本語のテキスト").unwrap();
    assert_eq!(buf.char_len(), 8, "8 chars even though 24 bytes");
    assert_invariants(&buf, "日本語のテキスト");

    assert_eq!(buf.slice(2, 5), Ok("語のテ".to_string()));

    buf.insert(3, "中").unwrap();
    assert_eq!(buf.to_string(), "日本語中のテキスト");
    assert_eq!(buf.char_len(), 9);

    buf.delete(0, 2).unwrap();
    assert_eq!(buf.to_string(), "語中のテキスト");
    assert_eq!(buf.slice(0, 3), Ok("語中の".to_string()));
    assert_invariants(&buf, "語中のテキスト");
}

#[test]
fn emoji_and_combining_marks_count_as_scalar_values() {
    // ['🦀', 'e', U+0301 combining acute, 'x'] — 4 chars
    let mut buf = TextBuffer::from_text("🦀e\u{301}x");
    assert_eq!(buf.char_len(), 4);

    // inserting between the base letter and its combining mark is legal at
    // the char level
    buf.insert(2, "Z").unwrap();
    assert_eq!(buf.to_string(), "🦀eZ\u{301}x");
    assert_eq!(buf.char_len(), 5);

    buf.delete(0, 1).unwrap();
    assert_eq!(buf.to_string(), "eZ\u{301}x");
    assert_eq!(buf.slice(1, 3), Ok("Z\u{301}".to_string()));
    assert_invariants(&buf, "eZ\u{301}x");
}

#[test]
fn out_of_range_ops_error_and_leave_the_buffer_untouched() {
    let mut buf = TextBuffer::from_text("héllo");
    assert_eq!(buf.char_len(), 5);

    assert_eq!(
        buf.insert(6, "x"),
        Err(EditError::IndexOutOfBounds { index: 6, len: 5 })
    );
    assert_eq!(
        buf.slice(2, 9),
        Err(EditError::RangeOutOfBounds {
            start: 2,
            end: 9,
            len: 5
        })
    );
    assert_eq!(
        buf.delete(4, 9),
        Err(EditError::RangeOutOfBounds {
            start: 4,
            end: 9,
            len: 5
        })
    );
    assert_eq!(
        buf.slice(3, 2),
        Err(EditError::InvalidRange { start: 3, end: 2 })
    );
    assert_eq!(
        buf.delete(9, 4),
        Err(EditError::InvalidRange { start: 9, end: 4 }),
        "start > end wins over out-of-bounds"
    );

    // nothing above may have mutated the buffer
    assert_invariants(&buf, "héllo");

    buf.insert(5, "!").unwrap();
    assert_eq!(buf.to_string(), "héllo!");
}

#[test]
fn insert_at_the_very_end_is_legal() {
    let mut buf = TextBuffer::from_text("ab");
    buf.insert(2, "c").unwrap();
    assert_eq!(buf.to_string(), "abc");

    // empty text is a no-op but bounds are still checked
    buf.insert(3, "").unwrap();
    assert_eq!(buf.to_string(), "abc");
    assert_eq!(
        buf.insert(4, ""),
        Err(EditError::IndexOutOfBounds { index: 4, len: 3 })
    );
}

#[test]
fn chunks_split_on_growth_and_never_overflow_capacity() {
    let mut buf = TextBuffer::with_chunk_capacity(4);
    buf.insert(0, "abcdefghij").unwrap();
    assert_invariants(&buf, "abcdefghij");
    assert!(buf.chunk_count() >= 3, "10 chars cannot fit in fewer than 3 chunks of 4");

    buf.insert(4, "XY").unwrap();
    assert_invariants(&buf, "abcdXYefghij");
    assert_eq!(buf.slice(2, 8), Ok("cdXYef".to_string()), "slice crosses chunk seams");

    // grow one char at a time and re-check the invariant every step
    let mut expected = String::from("abcdXYefghij");
    for ch in "klmnopqrstuvwxyz".chars() {
        let at = buf.char_len();
        buf.insert(at, &ch.to_string()).unwrap();
        expected.push(ch);
        assert_invariants(&buf, &expected);
    }
}

#[test]
fn delete_across_chunk_boundaries_cleans_up() {
    let mut buf = TextBuffer::with_chunk_capacity(3);
    buf.insert(0, "abcdefghij").unwrap();
    assert_invariants(&buf, "abcdefghij");

    buf.delete(2, 8).unwrap();
    assert_eq!(buf.to_string(), "abij");
    assert_invariants(&buf, "abij");

    buf.delete(0, buf.char_len()).unwrap();
    assert!(buf.is_empty());
    assert_eq!(buf.chunk_count(), 0, "an empty buffer holds no chunks");
    assert_invariants(&buf, "");
}

#[test]
fn slice_edges() {
    let buf = TextBuffer::from_text("αβγδε");
    assert_eq!(buf.slice(0, 5), Ok("αβγδε".to_string()));
    assert_eq!(buf.slice(2, 2), Ok(String::new()));
    assert_eq!(buf.slice(5, 5), Ok(String::new()), "empty slice at len is fine");
    assert_eq!(buf.slice(0, 1), Ok("α".to_string()));
    assert_eq!(buf.slice(4, 5), Ok("ε".to_string()));
}

#[test]
fn from_text_respects_the_default_capacity() {
    let text: String = "abcdefghij".repeat(8); // 80 chars
    let buf = TextBuffer::from_text(&text);
    assert_invariants(&buf, &text);
    assert!(buf.chunk_count() >= 3, "80 chars cannot fit in fewer than 3 chunks of 32");
}

#[test]
#[should_panic(expected = "chunk capacity")]
fn zero_chunk_capacity_panics() {
    let _ = TextBuffer::with_chunk_capacity(0);
}

#[test]
fn scripted_differential_run_against_a_vec_char_model() {
    #[derive(Clone, Copy)]
    enum Op {
        InsertAtHalf(&'static str),
        InsertAtStart(&'static str),
        InsertAtEnd(&'static str),
        InsertAtThird(&'static str),
        DeleteFront(usize),          // delete [0, n)
        DeleteMidQuarter,            // delete [len/4, 3*len/4)
        DeleteTailWindow,            // delete [len-5, len-1)
        DeleteAll,
        DeleteInner,                 // delete [1, len-1)
    }
    use Op::*;

    let script: &[Op] = &[
        InsertAtStart("The quick brown fox"),
        InsertAtHalf("🦀🚀"),
        InsertAtStart("日本語テキスト "),
        DeleteFront(7),
        InsertAtEnd(" — end"),
        InsertAtThird("αβγδ"),
        DeleteTailWindow,
        DeleteMidQuarter,
        InsertAtStart("汉"),
        InsertAtEnd("字"),
        DeleteInner,
        InsertAtHalf("middle 中间 text"),
        DeleteAll,
        InsertAtEnd("rebuilt 🧵 buffer"),
        InsertAtHalf("e\u{301}"),
        DeleteFront(1),
    ];

    let mut buf = TextBuffer::with_chunk_capacity(5);
    let mut model: Vec<char> = Vec::new();

    for (step, op) in script.iter().enumerate() {
        let len = model.len();
        match *op {
            InsertAtHalf(s) => {
                buf.insert(len / 2, s).unwrap();
                model.splice(len / 2..len / 2, s.chars());
            }
            InsertAtStart(s) => {
                buf.insert(0, s).unwrap();
                model.splice(0..0, s.chars());
            }
            InsertAtEnd(s) => {
                buf.insert(len, s).unwrap();
                model.splice(len..len, s.chars());
            }
            InsertAtThird(s) => {
                buf.insert(len / 3, s).unwrap();
                model.splice(len / 3..len / 3, s.chars());
            }
            DeleteFront(n) => {
                buf.delete(0, n).unwrap();
                model.drain(0..n);
            }
            DeleteMidQuarter => {
                buf.delete(len / 4, 3 * len / 4).unwrap();
                model.drain(len / 4..3 * len / 4);
            }
            DeleteTailWindow => {
                buf.delete(len - 5, len - 1).unwrap();
                model.drain(len - 5..len - 1);
            }
            DeleteAll => {
                buf.delete(0, len).unwrap();
                model.drain(0..len);
            }
            DeleteInner => {
                buf.delete(1, len - 1).unwrap();
                model.drain(1..len - 1);
            }
        }
        let expected = model_string(&model);
        assert_invariants(&buf, &expected);

        // slice probe against the model
        let l = model.len();
        let (s, e) = (l / 4, l - l / 4);
        assert_eq!(
            buf.slice(s, e),
            Ok(model[s..e].iter().collect::<String>()),
            "slice probe after step {step}"
        );
        assert_eq!(buf.slice(0, l), Ok(expected));
    }

    // pin the final state exactly so a compensating pair of bugs can't hide
    assert_eq!(buf.to_string(), "ebuilt e\u{301}🧵 buffer");
    assert_eq!(buf.char_len(), 17);
}

#[test]
fn error_display_messages_are_stable() {
    assert_eq!(
        EditError::IndexOutOfBounds { index: 7, len: 3 }.to_string(),
        "char index 7 out of bounds (len 3)"
    );
    assert_eq!(
        EditError::RangeOutOfBounds {
            start: 2,
            end: 9,
            len: 4
        }
        .to_string(),
        "char range 2..9 out of bounds (len 4)"
    );
    assert_eq!(
        EditError::InvalidRange { start: 5, end: 2 }.to_string(),
        "invalid char range 5..2"
    );
    fn assert_error<E: std::error::Error>(_: &E) {}
    assert_error(&EditError::InvalidRange { start: 1, end: 0 });
}
