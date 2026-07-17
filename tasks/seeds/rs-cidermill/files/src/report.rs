use std::fmt::Write;

use crate::ledger::DayLedger;
use crate::tank::Tank;

const BOTTLE_ML: u32 = 750;
const BOTTLES_PER_CASE: u32 = 12;

/// Bottle count the day's accepted juice works out to, rounded down —
/// the bottling line does not fill short bottles.
pub fn BottleEquiv(litres: u32) -> u32 {
    litres * 1000 / BOTTLE_ML
}

/// Full cases the bottling line can pack, plus loose bottles left on the
/// bench for the tasting room.
pub fn CaseSplit(bottles: u32) -> (u32, u32) {
    (bottles / BOTTLES_PER_CASE, bottles % BOTTLES_PER_CASE)
}

fn tray_share(total_l: u32, trays: u32) -> u32 {
    if trays == 0 {
        0
    } else {
        total_l / trays
    }
}

/// The chalkboard summary the floor lead reads out at close.
pub fn floor_summary(tanks: &[Tank], ledger: &DayLedger) -> String {
    let mut out = String::new();
    let mut total_stored = tanks.iter().map(|t| t.filled_l).sum::<u32>();
    writeln!(
        out,
        "pressed {} L into {} tanks ({} L stored)",
        ledger.accepted_l,
        tanks.len(),
        total_stored
    );
    for t in tanks {
        out.push_str(&format!("  {}: {} / {} L\n", t.label, t.filled_l, t.capacity_l));
    }
    if !ledger.rejected.is_empty() {
        out.push_str(&format!("  turned away: {} pour(s)\n", ledger.rejected.len()));
    }
    let bottles = BottleEquiv(ledger.accepted_l);
    let (cases, loose) = CaseSplit(bottles);
    out.push_str(&format!(
        "bottling: {} bottles = {} cases + {} loose",
        bottles, cases, loose
    ));
    out
}
