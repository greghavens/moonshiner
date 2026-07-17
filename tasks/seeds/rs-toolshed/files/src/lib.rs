//! Ledger for the neighbourhood tool-lending shed: what we own, who has
//! what out, and the intake log the volunteers read back at the monthly
//! count.

/// One tool in the shed.
#[derive(Debug, Clone, PartialEq)]
pub struct Tool {
    pub name: String,
    pub category: String,
}

/// An open loan: which tool a member took and how long it's been out.
#[derive(Debug, Clone, PartialEq)]
pub struct Loan {
    pub tool: String,
    pub member: String,
    pub weeks_out: u32,
}

/// A member of the shed, with their current number of open loans.
#[derive(Debug, Clone)]
pub struct Member {
    pub name: String,
    pub open_loans: u32,
}

/// Record a box of donated tools: each goes into the inventory and gets
/// a line in the intake log, in donation order.
pub fn record_donations(
    inventory: &mut Vec<Tool>,
    intake_log: &mut Vec<String>,
    donations: Vec<Tool>,
) {
    for tool in donations {
        inventory.push(tool);
        intake_log.push(format!("received {} ({})", tool.name, tool.category));
    }
}

/// The notice-board line naming the member with the most tools out,
/// written while we also fold in this month's sign-ups (who join with
/// zero loans, so they can't change the answer).
pub fn notice_line(members: &mut Vec<Member>, signups: Vec<Member>) -> String {
    let busiest = members.iter().max_by_key(|m| m.open_loans).unwrap();
    for m in signups {
        members.push(m);
    }
    format!("most tools out: {} ({})", busiest.name, busiest.open_loans)
}

/// Of two candidate storage labels, pick the one to engrave: the
/// shorter (engraving is priced per character); ties go to the first.
pub fn pick_label(a: &str, b: &str) -> &str {
    if b.len() < a.len() {
        b
    } else {
        a
    }
}

/// The shelf card pinned above a tool's slot.
pub fn shelf_card(tool: &Tool) -> String {
    let mut card = String::from("TOOL: ");
    card.push_str(tool.name);
    card.push_str(" [");
    card.push_str(tool.category.clone());
    card.push(']');
    card
}

/// Cull loans that have been out past the lending limit, keeping the
/// rest in order; returns the culled tool names for reminder postcards.
pub fn cull_overdue(loans: &mut Vec<Loan>, limit_weeks: u32) -> Vec<String> {
    let mut culled = Vec::new();
    for (i, loan) in loans.iter().enumerate() {
        if loan.weeks_out > limit_weeks {
            culled.push(loan.tool.clone());
            loans.remove(i);
        }
    }
    culled
}
