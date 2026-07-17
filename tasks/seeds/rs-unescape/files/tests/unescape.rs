use rs_unescape::{unescape, EscapeError, EscapeErrorKind};

fn err(kind: EscapeErrorKind, start: usize, end: usize) -> EscapeError {
    EscapeError { kind, start, end }
}

#[test]
fn plain_text_passes_through() {
    let cases = [
        ("", ""),
        ("no escapes here", "no escapes here"),
        ("café → bar", "café → bar"),
    ];
    for (input, want) in cases {
        assert_eq!(unescape(input).as_deref(), Ok(want), "input {input:?}");
    }
}

#[test]
fn simple_escapes_decode() {
    let cases = [
        (r"line1\nline2", "line1\nline2"),
        (r"tab\there", "tab\there"),
        (r"cr\rlf", "cr\rlf"),
        (r"a\\b", "a\\b"),
        (r#"say \"hi\""#, "say \"hi\""),
        (r"it\'s", "it's"),
        (r"nul\0end", "nul\0end"),
    ];
    for (input, want) in cases {
        assert_eq!(unescape(input).as_deref(), Ok(want), "input {input:?}");
    }
}

#[test]
fn hex_and_unicode_escapes_decode() {
    let cases = [
        (r"\x41\x20\x7a", "A z"),
        (r"\x0a", "\n"),
        (r"\u{e9}", "é"),
        (r"\u{E9}", "é"),
        (r"\u{2192}", "→"),
        (r"\u{1F600}", "😀"),
        (r"\u{0}", "\0"),
        (r"\u{10FFFF}", "\u{10FFFF}"),
        (r"mix \u{48}\x49\u{21}", "mix HI!"),
    ];
    for (input, want) in cases {
        assert_eq!(unescape(input).as_deref(), Ok(want), "input {input:?}");
    }
}

#[test]
fn error_kinds_and_spans_are_exact() {
    let cases = [
        (r"oops\", err(EscapeErrorKind::TrailingBackslash, 4, 5)),
        (r"bad \q here", err(EscapeErrorKind::UnknownEscape('q'), 4, 6)),
        ("a\\🙂", err(EscapeErrorKind::UnknownEscape('🙂'), 1, 6)),
        (r"\u00e9", err(EscapeErrorKind::BareUnicode, 0, 2)),
        (r"x\u", err(EscapeErrorKind::BareUnicode, 1, 3)),
        (r"\u{}", err(EscapeErrorKind::EmptyUnicode, 0, 4)),
        (r"\u{12", err(EscapeErrorKind::UnterminatedUnicode, 0, 5)),
        (r"\u{12g4}", err(EscapeErrorKind::BadHexDigit('g'), 0, 6)),
        (r"\u{1234567}", err(EscapeErrorKind::OverlongUnicode, 0, 10)),
        (
            r"\u{110000}",
            err(EscapeErrorKind::OutOfRange(0x110000), 0, 10),
        ),
        (
            r"\u{D800}",
            err(EscapeErrorKind::LoneSurrogate(0xD800), 0, 8),
        ),
        (
            r"\u{dfff}",
            err(EscapeErrorKind::LoneSurrogate(0xDFFF), 0, 8),
        ),
        (r"\xZZ", err(EscapeErrorKind::BadHexDigit('Z'), 0, 3)),
        (r"\x4", err(EscapeErrorKind::TruncatedByte, 0, 3)),
        (r"\x", err(EscapeErrorKind::TruncatedByte, 0, 2)),
        (r"\xE9", err(EscapeErrorKind::ByteOutOfRange(0xE9), 0, 4)),
        (r"\n\x80", err(EscapeErrorKind::ByteOutOfRange(0x80), 2, 6)),
    ];
    for (input, want) in cases {
        assert_eq!(unescape(input), Err(want), "input {input:?}");
    }
}

#[test]
fn spans_are_byte_offsets_not_char_offsets() {
    // é in the prefix is two bytes long, so the backslash sits at byte 7.
    assert_eq!(
        unescape("héllo \\q"),
        Err(err(EscapeErrorKind::UnknownEscape('q'), 7, 9))
    );
}

#[test]
fn first_error_wins() {
    assert_eq!(
        unescape(r"fine\nthen \xZZ and \u{} later"),
        Err(err(EscapeErrorKind::BadHexDigit('Z'), 11, 14))
    );
}

#[test]
fn error_type_is_a_real_error() {
    let e = unescape(r"\u{D800}").unwrap_err();
    assert_eq!(e.to_string(), "invalid escape at bytes 0..8");
    let boxed: Box<dyn std::error::Error> = Box::new(e);
    assert_eq!(boxed.to_string(), "invalid escape at bytes 0..8");
}

#[test]
fn error_values_are_plain_data() {
    // Copy + Clone + Eq: the fixture linter stores and compares these.
    let e = err(EscapeErrorKind::OutOfRange(0x110000), 0, 10);
    let copy = e;
    assert_eq!(copy, e.clone());
}
