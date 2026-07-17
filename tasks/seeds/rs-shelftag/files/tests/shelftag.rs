use rs_shelftag::{parse, same_bay, sort_key, spine_label, ShelfTag};

#[test]
fn parses_a_clean_sticker() {
    let line = String::from("FIC-ADK-03");
    let tag = parse(&line).expect("sticker should parse");
    assert_eq!(tag.section, "FIC");
    assert_eq!(tag.cluster, "ADK");
    assert_eq!(tag.slot, 3);
}

#[test]
fn stickers_missing_parts_are_rejected() {
    assert_eq!(parse("REF-OED"), None, "sticker missing its slot");
    assert_eq!(parse("FIC"), None, "sticker with only a section");
}

#[test]
fn misprints_are_rejected() {
    assert_eq!(parse("-ADK-03"), None, "empty section");
    assert_eq!(parse("FIC--03"), None, "empty cluster");
    assert_eq!(parse("FIC-ADK-xx"), None, "slot is not a number");
    assert_eq!(parse("FIC-ADK-300"), None, "slot beyond a u8 is a smudge");
}

#[test]
fn same_bay_compares_section_and_cluster() {
    let a = parse("FIC-ADK-03").unwrap();
    let b = parse("FIC-ADK-11").unwrap();
    let c = parse("FIC-BRO-03").unwrap();
    assert!(same_bay(&a, &b));
    assert!(!same_bay(&a, &c));
}

#[test]
fn spine_labels_zero_pad_the_slot() {
    let tag = ShelfTag { section: "NF", cluster: "910", slot: 7 };
    assert_eq!(spine_label(&tag), "NF-910-07");
    let wide = parse("FIC-ADK-11").unwrap();
    assert_eq!(spine_label(&wide), "FIC-ADK-11");
}

#[test]
fn sort_keys_order_the_trolley() {
    let mut tags = vec![
        parse("NF-910-02").unwrap(),
        parse("FIC-BRO-01").unwrap(),
        parse("FIC-ADK-11").unwrap(),
        parse("FIC-ADK-03").unwrap(),
    ];
    tags.sort_by_key(|t| sort_key(t));
    let labels: Vec<String> = tags.iter().map(spine_label).collect();
    assert_eq!(labels, ["FIC-ADK-03", "FIC-ADK-11", "FIC-BRO-01", "NF-910-02"]);
}
