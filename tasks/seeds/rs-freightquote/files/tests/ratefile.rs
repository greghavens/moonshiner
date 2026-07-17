//! Acceptance: the .rates sheet parser — grammar, typed values, document
//! order, and exact parse errors with 1-based line numbers.

use rs_freightquote::money::Money;
use rs_freightquote::ratefile::{ParseError, RateFile, Value};

fn err(src: &str) -> ParseError {
    match RateFile::parse(src) {
        Ok(_) => panic!("expected parse error for {:?}", src),
        Err(e) => e,
    }
}

fn check(src: &str, line: usize, message: &str) {
    let e = err(src);
    assert_eq!(e.line, line, "line for {:?}", src);
    assert_eq!(e.message, message, "message for {:?}", src);
}

#[test]
fn typed_values_parse() {
    let src = "[meta]\nname = \"mainline\"\n\n[limits]\nmax_zone = 9\nfloor = -2\nminimum = $89.00\nfuel = 12.5%\npeak = 4%\ntiny = 0.25%\nactive = true\nclosed = false\n";
    let f = RateFile::parse(src).unwrap();
    assert_eq!(f.get("meta", "name"), Some(&Value::Str("mainline".to_string())));
    assert_eq!(f.get("limits", "max_zone"), Some(&Value::Int(9)));
    assert_eq!(f.get("limits", "floor"), Some(&Value::Int(-2)));
    assert_eq!(f.get("limits", "minimum"), Some(&Value::Money(Money::from_cents(8900))));
    assert_eq!(f.get("limits", "fuel"), Some(&Value::Percent(1250)));
    assert_eq!(f.get("limits", "peak"), Some(&Value::Percent(400)));
    assert_eq!(f.get("limits", "tiny"), Some(&Value::Percent(25)));
    assert_eq!(f.get("limits", "active"), Some(&Value::Bool(true)));
    assert_eq!(f.get("limits", "closed"), Some(&Value::Bool(false)));
    assert_eq!(f.get("limits", "nope"), None);
    assert_eq!(f.get("nowhere", "x"), None);
}

#[test]
fn comments_full_line_and_inline() {
    let src = "# top comment\n[hub]   # section comment\ncode = \"A#B\"  # hash inside a string is literal\nzone = 3 # tail\n";
    let f = RateFile::parse(src).unwrap();
    assert_eq!(f.get("hub", "code"), Some(&Value::Str("A#B".to_string())));
    assert_eq!(f.get("hub", "zone"), Some(&Value::Int(3)));
}

#[test]
fn blank_lines_and_spacing_tolerated() {
    let src = "\n   \n[ hub ]\n   port   =    8\n\n";
    let f = RateFile::parse(src).unwrap();
    assert_eq!(f.get("hub", "port"), Some(&Value::Int(8)));
}

#[test]
fn document_order_preserved() {
    let src = "[zeta]\nb = 1\na = 2\n\n[alpha]\nc = 3\n";
    let f = RateFile::parse(src).unwrap();
    assert_eq!(f.sections(), vec!["zeta", "alpha"]);
    assert_eq!(f.keys("zeta"), vec!["b", "a"]);
    assert_eq!(f.keys("alpha"), vec!["c"]);
    assert_eq!(f.keys("nope"), Vec::<&str>::new());
}

#[test]
fn key_before_any_section() {
    check("x = 1\n[a]\n", 1, "key before any section");
}

#[test]
fn malformed_section_headers() {
    check("[bad name]\n", 1, "malformed section header");
    check("[]\n", 1, "malformed section header");
    check("[hub] extra\n", 1, "malformed section header");
    check("[hub\n", 1, "malformed section header");
}

#[test]
fn duplicate_section_reported_at_second_header() {
    check("[hub]\na = 1\n\n[hub]\nb = 2\n", 4, "duplicate section 'hub'");
}

#[test]
fn missing_equals() {
    check("[hub]\nport 8\n", 2, "expected '=' after key");
}

#[test]
fn malformed_key() {
    check("[hub]\npo rt = 8\n", 2, "malformed key");
    check("[hub]\n= 5\n", 2, "malformed key");
}

#[test]
fn unterminated_string() {
    check("[hub]\nname = \"open\n", 2, "unterminated string");
    // a hash inside an open string does not start a comment
    check("[hub]\nname = \"a # b\n", 2, "unterminated string");
}

#[test]
fn money_needs_dollar_and_exactly_two_decimals() {
    check("[a]\nx = $12.5\n", 2, "bad money amount");
    check("[a]\nx = $12\n", 2, "bad money amount");
    check("[a]\nx = $1,200.00\n", 2, "bad money amount");
}

#[test]
fn bare_decimal_is_not_a_value() {
    // there is no float type in this format: money carries '$', percent '%'
    check("[a]\nx = 12.50\n", 2, "unrecognized value");
}

#[test]
fn percent_allows_at_most_two_decimals() {
    check("[a]\nx = 12.345%\n", 2, "bad percent");
    check("[a]\nx = -4%\n", 2, "bad percent");
    check("[a]\nx = %\n", 2, "bad percent");
}

#[test]
fn empty_value_is_unrecognized() {
    check("[a]\nx =\n", 2, "unrecognized value");
    check("[a]\nx =   # only a comment\n", 2, "unrecognized value");
}

#[test]
fn trailing_characters_after_value() {
    check("[hub]\nzone = 3 junk\n", 2, "trailing characters after value");
    check("[hub]\nflag = true 1\n", 2, "trailing characters after value");
}

#[test]
fn duplicate_key_in_section() {
    check("[hub]\nzone = 1\nzone = 2\n", 3, "duplicate key 'zone' in section 'hub'");
    // same key in different sections is fine
    let f = RateFile::parse("[a]\nzone = 1\n\n[b]\nzone = 2\n").unwrap();
    assert_eq!(f.get("a", "zone"), Some(&Value::Int(1)));
    assert_eq!(f.get("b", "zone"), Some(&Value::Int(2)));
}

#[test]
fn error_display_format() {
    assert_eq!(err("x = 1\n").to_string(), "rates:1: key before any section");
    assert_eq!(err("[a]\ny = $9\n").to_string(), "rates:2: bad money amount");
}
