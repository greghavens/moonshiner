use crate::tank::{RejectedPour, Tank};

/// One pressing run: which tank the crew aimed the hose at, and how many
/// litres came off the press.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PressRun {
    pub tank_idx: usize,
    pub litres: u32,
}

/// End-of-day totals for the floor.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct DayLedger {
    pub accepted_l: u32,
    pub rejected: Vec<RejectedPour>,
}

/// Apply a day's press runs to the tanks, keeping score of what the
/// valves actually took.
pub fn run_day(tanks: &mut [Tank], runs: &[PressRun]) -> DayLedger {
    let mut ledger = DayLedger::default();
    for run in runs {
        tanks[run.tank_idx].pour(run.litres);
        ledger.accepted_l += run.litres;
    }
    ledger
}

/// After the last run, whatever is still sitting in the press tray gets
/// poured across to the overflow tank and goes on the same ledger.
pub fn close_day(overflow: &mut Tank, tray_l: u32, ledger: &mut DayLedger) {
    if tray_l == 0 {
        return;
    }
    overflow.pour(tray_l);
    ledger.accepted_l += tray_l;
}
