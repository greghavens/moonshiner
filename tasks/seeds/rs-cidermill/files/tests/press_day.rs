use rs_cidermill::ledger::{close_day, run_day, PressRun};
use rs_cidermill::report;
use rs_cidermill::tank::Tank;

fn floor() -> Vec<Tank> {
    vec![Tank::new("north", 500), Tank::new("south", 300)]
}

#[test]
fn accepted_and_refused_pours_are_scored_separately() {
    let mut tanks = floor();
    let runs = [
        PressRun { tank_idx: 0, litres: 400 },
        PressRun { tank_idx: 1, litres: 250 },
        PressRun { tank_idx: 0, litres: 200 },
        PressRun { tank_idx: 1, litres: 40 },
    ];
    let ledger = run_day(&mut tanks, &runs);
    assert_eq!(ledger.accepted_l, 690, "only juice the valve took counts");
    assert_eq!(tanks[0].filled_l, 400);
    assert_eq!(tanks[1].filled_l, 290);
    assert_eq!(ledger.rejected.len(), 1, "the refused pour goes on the ledger");
    assert_eq!(ledger.rejected[0].tank, "north");
    assert_eq!(ledger.rejected[0].litres, 200);
    assert_eq!(ledger.rejected[0].over_by_l, 100);
}

#[test]
fn a_refused_pour_leaves_the_tank_unchanged() {
    let mut tank = Tank::new("north", 500);
    let ledger = run_day(
        std::slice::from_mut(&mut tank),
        &[PressRun { tank_idx: 0, litres: 501 }],
    );
    assert_eq!(tank.filled_l, 0);
    assert_eq!(tank.headroom_l(), 500);
    assert_eq!(ledger.accepted_l, 0);
    assert_eq!(ledger.rejected.len(), 1);
}

#[test]
fn close_day_records_tray_juice_the_overflow_tank_cannot_take() {
    let mut tanks = floor();
    let mut ledger = run_day(&mut tanks, &[PressRun { tank_idx: 1, litres: 280 }]);
    // south has 20 L of headroom left; 60 L off the tray will not fit
    close_day(&mut tanks[1], 60, &mut ledger);
    assert_eq!(ledger.accepted_l, 280, "lost tray juice must not be counted");
    assert_eq!(ledger.rejected.len(), 1);
    assert_eq!(
        ledger.rejected[0].to_string(),
        "south refused 60 L (40 L over capacity)"
    );
}

#[test]
fn close_day_pours_a_fitting_tray_into_the_overflow_tank() {
    let mut tanks = floor();
    let mut ledger = run_day(&mut tanks, &[PressRun { tank_idx: 0, litres: 100 }]);
    close_day(&mut tanks[1], 50, &mut ledger);
    assert_eq!(ledger.accepted_l, 150);
    assert_eq!(tanks[1].filled_l, 50);
    assert!(ledger.rejected.is_empty());
}

#[test]
fn floor_summary_reads_like_the_chalkboard() {
    let mut tanks = floor();
    let runs = [
        PressRun { tank_idx: 0, litres: 450 },
        PressRun { tank_idx: 1, litres: 100 },
        PressRun { tank_idx: 0, litres: 100 },
    ];
    let ledger = run_day(&mut tanks, &runs);
    let summary = report::floor_summary(&tanks, &ledger);
    assert_eq!(
        summary,
        "pressed 550 L into 2 tanks (550 L stored)\n\
         \x20 north: 450 / 500 L\n\
         \x20 south: 100 / 300 L\n\
         \x20 turned away: 1 pour(s)\n\
         bottling: 733 bottles = 61 cases + 1 loose"
    );
}

#[test]
fn clean_day_summary_has_no_turned_away_line() {
    let mut tanks = floor();
    let runs = [
        PressRun { tank_idx: 0, litres: 120 },
        PressRun { tank_idx: 1, litres: 60 },
    ];
    let ledger = run_day(&mut tanks, &runs);
    let summary = report::floor_summary(&tanks, &ledger);
    assert_eq!(
        summary,
        "pressed 180 L into 2 tanks (180 L stored)\n\
         \x20 north: 120 / 500 L\n\
         \x20 south: 60 / 300 L\n\
         bottling: 240 bottles = 20 cases + 0 loose"
    );
}

#[test]
fn bottle_math_answers_under_old_and_new_names() {
    // The packing-line exporter still links against the old names; the
    // snake_case ones are the ones new code should use.
    assert_eq!(report::bottle_equiv(15), 20);
    assert_eq!(report::BottleEquiv(15), 20);
    assert_eq!(report::case_split(20), (1, 8));
    assert_eq!(report::CaseSplit(20), (1, 8));
    assert_eq!(report::bottle_equiv(0), 0);
    assert_eq!(report::case_split(0), (0, 0));
}
