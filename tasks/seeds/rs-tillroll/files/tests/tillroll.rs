use rs_tillroll::{is_valid_header, matches, parse_export, parse_line, total_cents, Sale};

const EXPORT: &str = "CAFÉ TILL v2
ITEM;Flat White;2;3.80
TOTAL;;;
ITEM;Café au lait;1;4.05
note: drawer counted
ITEM;Oat Croissant;3;2.10
";

#[test]
fn recognizes_the_terminal_header() {
    assert!(is_valid_header("CAFÉ TILL v2"));
    assert!(!is_valid_header("CAFE TILL v2"));
    assert!(!is_valid_header("CAFÉ TILL v1"));
    assert!(!is_valid_header(""));
}

#[test]
fn matcher_handles_digits_dots_and_repeats() {
    assert!(matches(r"v\d+", "v12"));
    assert!(!matches(r"v\d+", "v"));
    assert!(matches(r"\d\d.\d\d", "12:45"));
    assert!(matches(r"log\.txt", "log.txt"));
    assert!(!matches(r"log\.txt", "logstxt"));
    assert!(matches(r"a\\b", "a\\b"));
    assert!(!matches(r"\d+", "12x"));
}

#[test]
fn parses_a_record_line() {
    assert_eq!(
        parse_line("ITEM;Flat White;2;3.80"),
        Some(Sale {
            name: "Flat White".to_string(),
            qty: 2,
            price_cents: 380,
        })
    );
}

#[test]
fn skips_non_record_lines() {
    assert_eq!(parse_line("TOTAL;;;"), None);
    assert_eq!(parse_line("note: drawer counted"), None);
    assert_eq!(parse_line("ITEM;No Price;1;free"), None);
    assert_eq!(parse_line("ITEM;Short Price;1;3.8"), None);
    assert_eq!(parse_line("ITEM;Word Qty;two;3.80"), None);
    assert_eq!(parse_line(""), None);
}

#[test]
fn imports_the_whole_roll() {
    let sales = parse_export(EXPORT).expect("a genuine export must import");
    assert_eq!(sales.len(), 3, "sales: {sales:?}");
    assert_eq!(
        sales[0],
        Sale {
            name: "Flat White".to_string(),
            qty: 2,
            price_cents: 380,
        }
    );
    assert_eq!(sales[1].name, "Café au lait");
    assert_eq!(sales[1].price_cents, 405);
    assert_eq!(sales[2].name, "Oat Croissant");
    assert_eq!(sales[2].price_cents, 210);
    assert_eq!(total_cents(&sales), 2 * 380 + 405 + 3 * 210);
}

#[test]
fn rejects_files_from_other_registers() {
    assert!(parse_export("SHOPTILL v1\nITEM;Tea;1;2.00\n").is_err());
    assert!(parse_export("").is_err());
}
