//! Parsing contract: the TOML-lite grammar, typed values, document-order
//! introspection, and exact parse errors with 1-based line numbers.

use rs_tomlite::{Document, ParseError, ParseErrorKind, Value};

#[test]
fn values_of_every_type_parse() {
    let src = "name = \"svc\"\ncount = -12\nrate = 0.25\ndebug = true\nports = [8080, 8081]\nlabels = [\"a\", \"b\"]\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(doc.get("", "name"), Some(&Value::Str("svc".to_string())));
    assert_eq!(doc.get("", "count"), Some(&Value::Int(-12)));
    assert_eq!(doc.get("", "rate"), Some(&Value::Float(0.25)));
    assert_eq!(doc.get("", "debug"), Some(&Value::Bool(true)));
    assert_eq!(
        doc.get("", "ports"),
        Some(&Value::Array(vec![Value::Int(8080), Value::Int(8081)]))
    );
    assert_eq!(
        doc.get("", "labels"),
        Some(&Value::Array(vec![
            Value::Str("a".to_string()),
            Value::Str("b".to_string())
        ]))
    );
}

#[test]
fn keys_before_any_header_belong_to_the_root_table() {
    let src = "version = 3\n\n[server]\nport = 80\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(doc.get("", "version"), Some(&Value::Int(3)));
    assert_eq!(doc.get("server", "port"), Some(&Value::Int(80)));
    assert_eq!(doc.get("server", "version"), None);
}

#[test]
fn dotted_headers_are_plain_table_names() {
    let src = "[service.http]\nport = 80\n\n[service.grpc]\nport = 81\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(
        doc.tables(),
        vec!["service.http".to_string(), "service.grpc".to_string()]
    );
    assert_eq!(doc.get("service.http", "port"), Some(&Value::Int(80)));
    assert_eq!(doc.get("service.grpc", "port"), Some(&Value::Int(81)));
    // "service" itself was never declared and holds nothing.
    assert_eq!(doc.get("service", "port"), None);
}

#[test]
fn string_escapes_decode() {
    let src = "msg = \"a\\\"b\\\\c\\nd\\te\\rf\"\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(
        doc.get("", "msg"),
        Some(&Value::Str("a\"b\\c\nd\te\rf".to_string()))
    );
}

#[test]
fn comments_and_blank_lines_are_tolerated() {
    let src = "# top comment\n   # indented comment\n\nx = 1\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(doc.get("", "x"), Some(&Value::Int(1)));
    assert_eq!(doc.keys(""), vec!["x".to_string()]);
}

#[test]
fn tables_and_keys_report_document_order() {
    let src = "top = 1\n\n[zeta]\nx = 1\ny = 2\n\n[alpha]\nz = 3\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(
        doc.tables(),
        vec!["".to_string(), "zeta".to_string(), "alpha".to_string()]
    );
    assert_eq!(doc.keys("zeta"), vec!["x".to_string(), "y".to_string()]);
    assert_eq!(doc.keys(""), vec!["top".to_string()]);
    assert_eq!(doc.keys("nope"), Vec::<String>::new());
}

#[test]
fn root_table_is_listed_only_when_it_has_keys() {
    let doc = Document::parse("[a]\nx = 1\n").unwrap();
    assert_eq!(doc.tables(), vec!["a".to_string()]);
}

#[test]
fn empty_array_and_empty_string_parse() {
    let src = "empty = []\nblank = \"\"\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(doc.get("", "empty"), Some(&Value::Array(vec![])));
    assert_eq!(doc.get("", "blank"), Some(&Value::Str(String::new())));
}

#[test]
fn get_of_missing_key_or_table_is_none() {
    let doc = Document::parse("[a]\nx = 1\n").unwrap();
    assert_eq!(doc.get("a", "y"), None);
    assert_eq!(doc.get("b", "x"), None);
}

#[test]
fn duplicate_table_is_rejected_with_its_line() {
    let src = "[a]\nx = 1\n\n[b]\ny = 2\n\n[a]\nz = 3\n";
    assert_eq!(
        Document::parse(src).unwrap_err(),
        ParseError {
            line: 7,
            kind: ParseErrorKind::DuplicateTable {
                name: "a".to_string()
            }
        }
    );
}

#[test]
fn duplicate_key_within_a_table_is_rejected() {
    let src = "[a]\nport = 1\nport = 2\n";
    assert_eq!(
        Document::parse(src).unwrap_err(),
        ParseError {
            line: 3,
            kind: ParseErrorKind::DuplicateKey {
                key: "port".to_string()
            }
        }
    );
    // The same key in a different table is fine.
    assert!(Document::parse("[a]\nport = 1\n\n[b]\nport = 2\n").is_ok());
}

#[test]
fn a_line_without_equals_is_flagged() {
    let src = "[a]\nthis line has no equals\n";
    assert_eq!(
        Document::parse(src).unwrap_err(),
        ParseError {
            line: 2,
            kind: ParseErrorKind::MissingEquals
        }
    );
}

#[test]
fn malformed_keys_are_flagged() {
    assert_eq!(
        Document::parse("b@d = 1\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadKey
        }
    );
    assert_eq!(
        Document::parse("spaced key = 1\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadKey
        }
    );
}

#[test]
fn malformed_headers_are_flagged() {
    assert_eq!(
        Document::parse("[unclosed\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadHeader
        }
    );
    assert_eq!(
        Document::parse("x = 1\n[]\n").unwrap_err(),
        ParseError {
            line: 2,
            kind: ParseErrorKind::BadHeader
        }
    );
    assert_eq!(
        Document::parse("[a b]\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadHeader
        }
    );
}

#[test]
fn integer_underscores_are_not_supported() {
    assert_eq!(
        Document::parse("x = 1_000\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadValue
        }
    );
}

#[test]
fn floats_require_digits_on_both_sides_of_the_dot() {
    assert_eq!(
        Document::parse("x = .5\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadValue
        }
    );
    assert_eq!(
        Document::parse("y = 1.\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadValue
        }
    );
}

#[test]
fn unterminated_strings_are_flagged() {
    assert_eq!(
        Document::parse("x = \"abc\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::UnterminatedString
        }
    );
}

#[test]
fn unknown_escapes_are_flagged() {
    assert_eq!(
        Document::parse("x = \"a\\qb\"\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadEscape
        }
    );
}

#[test]
fn inline_comments_are_not_supported() {
    assert_eq!(
        Document::parse("port = 8080 # nope\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::TrailingChars
        }
    );
}

#[test]
fn trailing_garbage_after_complete_values_is_flagged() {
    assert_eq!(
        Document::parse("x = \"a\" b\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::TrailingChars
        }
    );
    assert_eq!(
        Document::parse("y = [1] 2\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::TrailingChars
        }
    );
}

#[test]
fn unrecognized_and_empty_values_are_flagged() {
    assert_eq!(
        Document::parse("x = nope\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadValue
        }
    );
    assert_eq!(
        Document::parse("x =\n").unwrap_err(),
        ParseError {
            line: 1,
            kind: ParseErrorKind::BadValue
        }
    );
}

#[test]
fn error_display_is_line_prefixed() {
    assert_eq!(
        ParseError {
            line: 4,
            kind: ParseErrorKind::DuplicateKey {
                key: "port".to_string()
            }
        }
        .to_string(),
        "line 4: duplicate key 'port'"
    );
    assert_eq!(
        ParseError {
            line: 2,
            kind: ParseErrorKind::MissingEquals
        }
        .to_string(),
        "line 2: expected '=' after key"
    );
    assert_eq!(
        ParseError {
            line: 9,
            kind: ParseErrorKind::TrailingChars
        }
        .to_string(),
        "line 9: unexpected trailing characters"
    );
    assert_eq!(
        ParseError {
            line: 1,
            kind: ParseErrorKind::DuplicateTable {
                name: "a".to_string()
            }
        }
        .to_string(),
        "line 1: duplicate table 'a'"
    );
}
