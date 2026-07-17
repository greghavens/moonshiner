//! Round-trip and edit contract: render() reproduces the source byte for
//! byte, and set() touches exactly the lines it must — nothing else moves.

use rs_tomlite::{Document, Value};

#[test]
fn render_round_trips_byte_identically() {
    let src = "# deployment config\n\nversion = 3\n\n[server]\nhost=\"a.example\"\nport   =   8080\n\n# retry knobs\n[server.retry]\nlimit = 4\nbackoff = 1.5\nenabled = true\ntags = [\"edge\", \"eu-west\"]\n";
    let doc = Document::parse(src).unwrap();
    assert_eq!(doc.render(), src);
}

#[test]
fn round_trip_preserves_final_newline_presence() {
    let without = "a = 1\nb = 2";
    assert_eq!(Document::parse(without).unwrap().render(), without);
    let with = "a = 1\nb = 2\n";
    assert_eq!(Document::parse(with).unwrap().render(), with);
    assert_eq!(Document::parse("").unwrap().render(), "");
}

#[test]
fn set_existing_key_rewrites_only_that_line() {
    let src = "# cfg\n[server]\nhost=\"a.example\"\nport   =   8080\n# tail comment\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("server", "port", Value::Int(9090));
    // The edited line is canonicalized; every other byte survives, including
    // the no-spaces style of the host line and both comments.
    assert_eq!(
        doc.render(),
        "# cfg\n[server]\nhost=\"a.example\"\nport = 9090\n# tail comment\n"
    );
    assert_eq!(doc.get("server", "port"), Some(&Value::Int(9090)));
}

#[test]
fn set_new_key_lands_after_the_tables_last_key() {
    let src = "[server]\nhost = \"a\"\n\n# unrelated\n[client]\nretries = 2\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("server", "port", Value::Int(1));
    assert_eq!(
        doc.render(),
        "[server]\nhost = \"a\"\nport = 1\n\n# unrelated\n[client]\nretries = 2\n"
    );
}

#[test]
fn set_new_key_in_an_empty_table_lands_after_its_header() {
    let src = "[server]\n\n[client]\nx = 1\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("server", "host", Value::Str("h".to_string()));
    assert_eq!(doc.render(), "[server]\nhost = \"h\"\n\n[client]\nx = 1\n");
}

#[test]
fn set_in_a_new_table_appends_a_blank_separated_section() {
    let src = "[a]\nx = 1\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("metrics", "enabled", Value::Bool(true));
    assert_eq!(doc.render(), "[a]\nx = 1\n\n[metrics]\nenabled = true\n");
}

#[test]
fn set_on_an_empty_document_creates_the_section_without_padding() {
    let mut doc = Document::parse("").unwrap();
    doc.set("metrics", "enabled", Value::Bool(true));
    assert_eq!(doc.render(), "[metrics]\nenabled = true");
}

#[test]
fn set_root_key_with_no_root_section_inserts_at_the_top() {
    let src = "# header comment\n[a]\nx = 1\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("", "version", Value::Int(2));
    assert_eq!(doc.render(), "version = 2\n# header comment\n[a]\nx = 1\n");
}

#[test]
fn set_root_key_extends_the_existing_root_section() {
    let src = "name = \"svc\"\n\n[a]\nx = 1\n";
    let mut doc = Document::parse(src).unwrap();
    doc.set("", "owner", Value::Str("ops".to_string()));
    assert_eq!(
        doc.render(),
        "name = \"svc\"\nowner = \"ops\"\n\n[a]\nx = 1\n"
    );
}

#[test]
fn canonical_floats_keep_a_decimal_point() {
    let mut doc = Document::parse("[t]\n").unwrap();
    doc.set("t", "rate", Value::Float(8.0));
    doc.set("t", "half", Value::Float(2.5));
    assert_eq!(doc.render(), "[t]\nrate = 8.0\nhalf = 2.5\n");
    // Re-parsing must see floats, not ints.
    let doc2 = Document::parse(&doc.render()).unwrap();
    assert_eq!(doc2.get("t", "rate"), Some(&Value::Float(8.0)));
    assert_eq!(doc2.get("t", "half"), Some(&Value::Float(2.5)));
}

#[test]
fn canonical_strings_escape_what_they_must() {
    let mut doc = Document::parse("[t]\n").unwrap();
    doc.set("t", "msg", Value::Str("a\"b\\c\nd".to_string()));
    assert_eq!(doc.render(), "[t]\nmsg = \"a\\\"b\\\\c\\nd\"\n");
    let doc2 = Document::parse(&doc.render()).unwrap();
    assert_eq!(doc2.get("t", "msg"), Some(&Value::Str("a\"b\\c\nd".to_string())));
}

#[test]
fn canonical_arrays_are_comma_space_separated() {
    let mut doc = Document::parse("[t]\n").unwrap();
    doc.set(
        "t",
        "ports",
        Value::Array(vec![Value::Int(1), Value::Int(2), Value::Int(3)]),
    );
    assert_eq!(doc.render(), "[t]\nports = [1, 2, 3]\n");
}

#[test]
fn edits_survive_a_parse_render_cycle() {
    let mut doc = Document::parse("[t]\na = 1\n").unwrap();
    doc.set("t", "a", Value::Int(10));
    doc.set("t", "b", Value::Bool(false));
    doc.set("other", "c", Value::Str("z".to_string()));
    let out = doc.render();
    let doc2 = Document::parse(&out).unwrap();
    assert_eq!(doc2.get("t", "a"), Some(&Value::Int(10)));
    assert_eq!(doc2.get("t", "b"), Some(&Value::Bool(false)));
    assert_eq!(doc2.get("other", "c"), Some(&Value::Str("z".to_string())));
    // Rendering is idempotent once canonicalized.
    assert_eq!(doc2.render(), out);
}

#[test]
fn tables_reflect_sections_created_by_set() {
    let mut doc = Document::parse("[a]\nx = 1\n").unwrap();
    doc.set("b", "y", Value::Int(2));
    assert_eq!(doc.tables(), vec!["a".to_string(), "b".to_string()]);
    assert_eq!(doc.keys("b"), vec!["y".to_string()]);
}
