//! Acceptance: money arithmetic, weight-break pricing (billed cwt, bracket
//! floors, deficit-weight rebilling, scale minimum), surcharge stacking
//! order/caps/waivers, quote errors, and schema validation messages.

use std::collections::BTreeSet;

use rs_freightquote::money::Money;
use rs_freightquote::quote::{quote, QuoteError, Shipment};
use rs_freightquote::tariff::Tariff;

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| s.to_string()).collect()
}

fn ship(weight: u32) -> Shipment {
    Shipment {
        reference: "Q-1".to_string(),
        origin: "AAA".to_string(),
        dest: "BBB".to_string(),
        weight_lbs: weight,
        flags: set(&[]),
        waivers: set(&[]),
    }
}

/// One lane AAA(z1) -> BBB(z2) on scale X; the body supplies scale_X and
/// any surcharge sections.
fn sheet(extra: &str) -> Tariff {
    let src = format!(
        "[meta]\nname = \"t\"\n\n[zones]\nAAA = 1\nBBB = 2\n\n[lanes]\n1-2 = \"X\"\n\n{}",
        extra
    );
    Tariff::parse(&src).unwrap()
}

fn cents(m: Money) -> i64 {
    m.cents()
}

// ---------------------------------------------------------------- money

#[test]
fn money_display() {
    assert_eq!(Money::from_cents(0).to_string(), "$0.00");
    assert_eq!(Money::from_cents(5).to_string(), "$0.05");
    assert_eq!(Money::from_cents(105).to_string(), "$1.05");
    assert_eq!(Money::from_cents(36290).to_string(), "$362.90");
    // no thousands separators, ever
    assert_eq!(Money::from_cents(100013).to_string(), "$1000.13");
}

#[test]
fn percent_of_rounds_half_up() {
    // 0.25% of $10.00 = 2.5c -> 3c
    assert_eq!(cents(Money::from_cents(1000).percent_of(25)), 3);
    // 0.24% of $10.00 = 2.4c -> 2c
    assert_eq!(cents(Money::from_cents(1000).percent_of(24)), 2);
    // 0.25% of $1.01 = 0.2525c -> 0c
    assert_eq!(cents(Money::from_cents(101).percent_of(25)), 0);
    // 12.5% of $328.00 = $41.00 exactly
    assert_eq!(cents(Money::from_cents(32800).percent_of(1250)), 4100);
}

// ------------------------------------------------------------- brackets

#[test]
fn billed_cwt_rounds_up_to_the_next_hundredweight() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n");
    for (weight, total) in [(1u32, 1000i64), (100, 1000), (101, 2000), (250, 3000)] {
        let q = quote(&t, &ship(weight)).unwrap();
        assert_eq!(cents(q.total), total, "weight {}", weight);
        assert_eq!(q.linehaul.cwt, ((weight + 99) / 100), "cwt for {}", weight);
    }
}

#[test]
fn bracket_floor_is_inclusive_at_the_break() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\nfrom_500 = $8.00\n");
    // exactly 500 lb takes the from_500 rate, no rebill needed
    let q = quote(&t, &ship(500)).unwrap();
    assert_eq!(q.linehaul.rate, Money::from_cents(800));
    assert_eq!(cents(q.total), 4000);
    assert!(q.linehaul.rebill.is_none());
}

#[test]
fn deficit_weight_rebills_at_the_cheaper_break() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\nfrom_500 = $8.00\n");
    // 499 lb: natural 5 cwt @ $10.00 = $50.00; billed as 500 lb -> $40.00
    let q = quote(&t, &ship(499)).unwrap();
    assert_eq!(q.linehaul.rate, Money::from_cents(1000));
    assert_eq!(cents(q.linehaul.base), 5000);
    let r = q.linehaul.rebill.as_ref().expect("rebill");
    assert_eq!(r.lbs, 500);
    assert_eq!(r.cwt, 5);
    assert_eq!(r.rate, Money::from_cents(800));
    assert_eq!(cents(r.cost), 4000);
    assert_eq!(cents(q.total), 4000);
}

#[test]
fn deficit_tie_keeps_the_natural_bracket() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $20.00\nfrom_1000 = $10.00\n");
    // 500 lb: natural 5 cwt @ $20.00 = $100.00; at 1000 lb: 10 @ $10.00 = $100.00
    let q = quote(&t, &ship(500)).unwrap();
    assert!(q.linehaul.rebill.is_none(), "a tie must not rebill");
    assert_eq!(cents(q.total), 10000);
}

#[test]
fn deficit_scans_every_higher_bracket() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $30.00\nfrom_500 = $28.00\nfrom_1000 = $10.00\n");
    // 400 lb: natural 4 @ $30 = $120; at 500: 5 @ $28 = $140 (worse);
    // at 1000: 10 @ $10 = $100 (best)
    let q = quote(&t, &ship(400)).unwrap();
    let r = q.linehaul.rebill.as_ref().expect("rebill");
    assert_eq!(r.lbs, 1000);
    assert_eq!(cents(q.total), 10000);
}

#[test]
fn minimum_charge_applies_after_deficit_rebilling() {
    let t = sheet("[scale_X]\nminimum = $15.00\nfrom_0 = $20.00\nfrom_1000 = $1.00\n");
    // 300 lb: natural 3 @ $20 = $60; rebill at 1000 lb -> $10; minimum $15 wins
    let q = quote(&t, &ship(300)).unwrap();
    let r = q.linehaul.rebill.as_ref().expect("rebill");
    assert_eq!(cents(r.cost), 1000);
    assert!(q.linehaul.minimum_applied);
    assert_eq!(cents(q.linehaul.total), 1500);
    assert_eq!(cents(q.total), 1500);
}

// ----------------------------------------------------------- surcharges

#[test]
fn surcharges_apply_in_sheet_order_on_the_running_total() {
    let scale = "[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n";
    // linehaul for 1000 lb: 10 cwt @ $10.00 = $100.00
    let pct_first = sheet(&format!(
        "{}\n[surcharge_FUEL]\nlabel = \"Fuel\"\nkind = \"percent\"\namount = 10%\n\n[surcharge_HAND]\nlabel = \"Handling\"\nkind = \"flat\"\namount = $5.00\n",
        scale
    ));
    let flat_first = sheet(&format!(
        "{}\n[surcharge_HAND]\nlabel = \"Handling\"\nkind = \"flat\"\namount = $5.00\n\n[surcharge_FUEL]\nlabel = \"Fuel\"\nkind = \"percent\"\namount = 10%\n",
        scale
    ));
    let a = quote(&pct_first, &ship(1000)).unwrap();
    // $100.00 +10% = $110.00, +$5.00 = $115.00
    assert_eq!(cents(a.total), 11500);
    let codes: Vec<&str> = a.surcharges.iter().map(|s| s.code.as_str()).collect();
    assert_eq!(codes, vec!["FUEL", "HAND"]);

    let b = quote(&flat_first, &ship(1000)).unwrap();
    // $100.00 +$5.00 = $105.00, +10% ($10.50) = $115.50
    assert_eq!(cents(b.total), 11550);
    let codes: Vec<&str> = b.surcharges.iter().map(|s| s.code.as_str()).collect();
    assert_eq!(codes, vec!["HAND", "FUEL"]);
}

#[test]
fn percent_surcharge_rounds_half_up() {
    let t = sheet(
        "[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n\n[surcharge_TINY]\nlabel = \"Tiny\"\nkind = \"percent\"\namount = 0.25%\n",
    );
    // linehaul $10.00; 0.25% = 2.5c -> 3c
    let q = quote(&t, &ship(100)).unwrap();
    assert_eq!(cents(q.surcharges[0].amount), 3);
    assert_eq!(cents(q.total), 1003);
}

#[test]
fn caps_limit_percent_and_flat_surcharges() {
    let t = sheet(
        "[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n\n[surcharge_PEAK]\nlabel = \"Peak\"\nkind = \"percent\"\namount = 10%\ncap = $4.00\n\n[surcharge_DOCK]\nlabel = \"Dock\"\nkind = \"flat\"\namount = $50.00\ncap = $20.00\n",
    );
    // linehaul $100.00; PEAK 10% = $10.00 capped to $4.00 -> $104.00;
    // DOCK $50.00 capped to $20.00 -> $124.00
    let q = quote(&t, &ship(1000)).unwrap();
    assert_eq!(cents(q.surcharges[0].amount), 400);
    assert!(q.surcharges[0].capped);
    assert_eq!(cents(q.surcharges[1].amount), 2000);
    assert!(q.surcharges[1].capped);
    assert_eq!(cents(q.total), 12400);
}

#[test]
fn requires_flag_missing_skips_the_surcharge_entirely() {
    let t = sheet(
        "[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n\n[surcharge_LIFT]\nlabel = \"Liftgate\"\nkind = \"flat\"\namount = $60.00\nrequires = \"liftgate\"\n",
    );
    let q = quote(&t, &ship(100)).unwrap();
    assert!(q.surcharges.is_empty(), "no flag, no entry at all");
    assert_eq!(cents(q.total), 1000);

    let mut s = ship(100);
    s.flags = set(&["liftgate"]);
    let q = quote(&t, &s).unwrap();
    assert_eq!(q.surcharges.len(), 1);
    assert_eq!(cents(q.total), 7000);
}

#[test]
fn contract_waiver_is_recorded_but_adds_nothing() {
    let t = sheet(
        "[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n\n[surcharge_FUEL]\nlabel = \"Fuel\"\nkind = \"percent\"\namount = 10%\n",
    );
    let mut s = ship(100);
    s.waivers = set(&["FUEL"]);
    let q = quote(&t, &s).unwrap();
    assert_eq!(q.surcharges.len(), 1);
    assert!(q.surcharges[0].waived);
    assert_eq!(cents(q.surcharges[0].amount), 0);
    assert_eq!(cents(q.total), 1000);
}

// --------------------------------------------------------- quote errors

#[test]
fn quote_errors_carry_exact_displays() {
    let t = sheet("[scale_X]\nminimum = $1.00\nfrom_0 = $10.00\n");

    let mut s = ship(100);
    s.origin = "ZZZ".to_string();
    let e = quote(&t, &s).unwrap_err();
    assert_eq!(e, QuoteError::UnknownStation("ZZZ".to_string()));
    assert_eq!(e.to_string(), "unknown station 'ZZZ'");

    // the lane table is directional: 1-2 exists, 2-1 does not
    let mut s = ship(100);
    s.origin = "BBB".to_string();
    s.dest = "AAA".to_string();
    let e = quote(&t, &s).unwrap_err();
    assert_eq!(e, QuoteError::NoLane(2, 1));
    assert_eq!(e.to_string(), "no lane from zone 2 to zone 1");

    let e = quote(&t, &ship(0)).unwrap_err();
    assert_eq!(e, QuoteError::BadWeight);
    assert_eq!(e.to_string(), "weight must be at least 1 lb");
}

// -------------------------------------------------------- schema errors

fn schema_err(src: &str) -> String {
    Tariff::parse(src).unwrap_err().to_string()
}

#[test]
fn schema_errors_for_structure() {
    assert_eq!(
        schema_err("[zones]\nAAA = 1\n\n[lanes]\n"),
        "schema: missing section [meta]"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[lanes]\n"),
        "schema: missing section [zones]"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n"),
        "schema: missing section [lanes]"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n\n[lanes]\n\n[extras]\nx = 1\n"),
        "schema: [extras]: unknown section"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\nowner = \"me\"\n\n[zones]\n\n[lanes]\n"),
        "schema: [meta] owner: unknown key"
    );
    // parse errors pass straight through the tariff layer
    assert_eq!(Tariff::parse("x = 1\n").unwrap_err().to_string(), "rates:1: key before any section");
}

#[test]
fn schema_errors_for_values() {
    assert_eq!(
        schema_err("[meta]\nname = 3\n\n[zones]\n\n[lanes]\n"),
        "schema: [meta] name: expected string"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\nAAA = 12\n\n[lanes]\n"),
        "schema: [zones] AAA: zone must be 1..9"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\nAlt = 1\n\n[lanes]\n"),
        "schema: [zones] Alt: bad station code"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\nAAA = 1\n\n[lanes]\n1x2 = \"A\"\n"),
        "schema: [lanes] 1x2: bad lane"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\nAAA = 1\n\n[lanes]\n1-2 = \"Q\"\n"),
        "schema: [lanes] 1-2: unknown scale 'Q'"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n\n[lanes]\n\n[scale_X]\nminimum = $1.00\nfrom_500 = $2.00\n"),
        "schema: [scale_X]: missing key 'from_0'"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n\n[lanes]\n\n[scale_X]\nminimum = $1.00\nfrom_0 = $2.00\nfrom_250 = $1.50\n"),
        "schema: [scale_X] from_250: floor must be a multiple of 100"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n\n[lanes]\n\n[surcharge_F]\nlabel = \"F\"\nkind = \"both\"\namount = $1.00\n"),
        "schema: [surcharge_F] kind: must be 'percent' or 'flat'"
    );
    assert_eq!(
        schema_err("[meta]\nname = \"t\"\n\n[zones]\n\n[lanes]\n\n[surcharge_F]\nlabel = \"F\"\nkind = \"percent\"\namount = $1.00\n"),
        "schema: [surcharge_F] amount: expected percent"
    );
}
