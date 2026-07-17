use rs_toolshed::*;

fn tool(name: &str, category: &str) -> Tool {
    Tool { name: name.to_string(), category: category.to_string() }
}

fn loan(tool: &str, member: &str, weeks_out: u32) -> Loan {
    Loan { tool: tool.to_string(), member: member.to_string(), weeks_out }
}

#[test]
fn donations_land_in_inventory_and_log_in_order() {
    let mut inventory = vec![tool("claw hammer", "hand")];
    let mut log = Vec::new();
    record_donations(
        &mut inventory,
        &mut log,
        vec![tool("jigsaw", "power"), tool("bow saw", "hand")],
    );
    assert_eq!(inventory.len(), 3);
    assert_eq!(inventory[1], tool("jigsaw", "power"));
    assert_eq!(inventory[2], tool("bow saw", "hand"));
    assert_eq!(
        log,
        vec![
            "received jigsaw (power)".to_string(),
            "received bow saw (hand)".to_string(),
        ]
    );
}

#[test]
fn notice_line_names_busiest_member_and_keeps_signups() {
    let mut members = vec![
        Member { name: "Priya".to_string(), open_loans: 2 },
        Member { name: "Sam".to_string(), open_loans: 5 },
        Member { name: "Ines".to_string(), open_loans: 1 },
    ];
    let line = notice_line(
        &mut members,
        vec![Member { name: "Noor".to_string(), open_loans: 0 }],
    );
    assert_eq!(line, "most tools out: Sam (5)");
    assert_eq!(members.len(), 4);
    assert_eq!(members[3].name, "Noor");
}

#[test]
fn pick_label_prefers_shorter_then_first() {
    assert_eq!(pick_label("crosscut saw", "saw"), "saw");
    assert_eq!(pick_label("axe", "ripsaw"), "axe");
    assert_eq!(pick_label("rake", "hoe4"), "rake");
}

#[test]
fn pick_label_result_outlives_the_longer_input() {
    let keep = String::from("drill");
    let picked;
    {
        let scratch = String::from("impact driver");
        picked = pick_label(&keep, &scratch).to_string();
    }
    assert_eq!(picked, "drill");
}

#[test]
fn shelf_card_layout() {
    let card = shelf_card(&tool("shoulder plane", "hand"));
    assert_eq!(card, "TOOL: shoulder plane [hand]");
}

#[test]
fn cull_overdue_removes_every_late_loan_in_order() {
    let mut loans = vec![
        loan("ladder", "Priya", 9),
        loan("sander", "Sam", 8),
        loan("level", "Ines", 2),
        loan("router", "Noor", 12),
    ];
    let culled = cull_overdue(&mut loans, 6);
    assert_eq!(culled, vec!["ladder".to_string(), "sander".to_string(), "router".to_string()]);
    assert_eq!(loans, vec![loan("level", "Ines", 2)]);
}

#[test]
fn cull_overdue_with_nothing_late_changes_nothing() {
    let mut loans = vec![loan("ladder", "Priya", 1)];
    assert!(cull_overdue(&mut loans, 6).is_empty());
    assert_eq!(loans.len(), 1);
}
