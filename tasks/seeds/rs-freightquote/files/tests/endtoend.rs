//! Acceptance: the shipped rate sheet loads from disk, quotes come out to
//! the cent, and the audit-trail export matches the filed format byte for
//! byte (trailing newline included).

use std::collections::BTreeSet;

use rs_freightquote::audit;
use rs_freightquote::quote::{quote, Shipment};
use rs_freightquote::tariff::Tariff;

fn set(items: &[&str]) -> BTreeSet<String> {
    items.iter().map(|s| s.to_string()).collect()
}

fn ship(reference: &str, origin: &str, dest: &str, weight: u32, flags: &[&str], waivers: &[&str]) -> Shipment {
    Shipment {
        reference: reference.to_string(),
        origin: origin.to_string(),
        dest: dest.to_string(),
        weight_lbs: weight,
        flags: set(flags),
        waivers: set(waivers),
    }
}

fn mainline() -> Tariff {
    Tariff::load("tariffs/mainline.rates").expect("fixture must load")
}

#[test]
fn fixture_loads_from_disk() {
    let t = mainline();
    assert_eq!(t.name, "mainline");
}

#[test]
fn corridor_quote_deficit_and_stacked_surcharges() {
    let t = mainline();
    let q = quote(&t, &ship("Q-1042", "ALT", "RVI", 1850, &["residential"], &[])).unwrap();
    assert_eq!(q.total.cents(), 43056);
    assert_eq!(
        audit::render(&q),
        concat!(
            "quote Q-1042 ALT -> RVI 1850 lb\n",
            "lane: zone 1 -> zone 3, scale B\n",
            "linehaul: 19 cwt @ $19.10/cwt = $362.90 | total $362.90\n",
            "deficit-weight: rebill at 2000 lb, 20 cwt @ $16.40/cwt = $328.00 | total $328.00\n",
            "surcharge FUEL (12.5% of $328.00): +$41.00 | total $369.00\n",
            "surcharge RESI (flat): +$45.00 | total $414.00\n",
            "surcharge PEAK (4% of $414.00): +$16.56 | total $430.56\n",
            "total: $430.56\n",
        )
    );
}

#[test]
fn waived_fuel_and_capped_peak() {
    let t = mainline();
    let q = quote(&t, &ship("Q-2077", "COV", "DRM", 6200, &["liftgate"], &["FUEL"])).unwrap();
    assert_eq!(q.total.cents(), 94000);
    assert_eq!(
        audit::render(&q),
        concat!(
            "quote Q-2077 COV -> DRM 6200 lb\n",
            "lane: zone 2 -> zone 3, scale B\n",
            "linehaul: 62 cwt @ $13.75/cwt = $852.50 | total $852.50\n",
            "surcharge FUEL: waived by contract | total $852.50\n",
            "surcharge LIFT (flat): +$62.50 | total $915.00\n",
            "surcharge PEAK (4% of $915.00, capped at $25.00): +$25.00 | total $940.00\n",
            "total: $940.00\n",
        )
    );
}

#[test]
fn minimum_charge_on_a_short_haul() {
    let t = mainline();
    let q = quote(&t, &ship("Q-3005", "ALT", "BRK", 120, &[], &[])).unwrap();
    assert_eq!(q.total.cents(), 10414);
    assert_eq!(
        audit::render(&q),
        concat!(
            "quote Q-3005 ALT -> BRK 120 lb\n",
            "lane: zone 1 -> zone 1, scale A\n",
            "linehaul: 2 cwt @ $16.80/cwt = $33.60 | total $33.60\n",
            "minimum-charge: raised to $89.00 | total $89.00\n",
            "surcharge FUEL (12.5% of $89.00): +$11.13 | total $100.13\n",
            "surcharge PEAK (4% of $100.13): +$4.01 | total $104.14\n",
            "total: $104.14\n",
        )
    );
}

#[test]
fn exactly_at_the_break_no_rebill() {
    let t = mainline();
    let q = quote(&t, &ship("Q-4400", "ALT", "RVI", 500, &[], &[])).unwrap();
    assert!(q.linehaul.rebill.is_none());
    assert_eq!(q.total.cents(), 11174);
    let out = audit::render(&q);
    assert!(out.contains("linehaul: 5 cwt @ $19.10/cwt = $95.50 | total $95.50\n"), "got:\n{}", out);
    assert!(!out.contains("deficit-weight"), "got:\n{}", out);
}

#[test]
fn one_pound_under_the_break_rebills_to_it() {
    let t = mainline();
    let q = quote(&t, &ship("Q-4401", "ALT", "RVI", 499, &[], &[])).unwrap();
    assert_eq!(q.total.cents(), 11174, "same money as parking at the break");
    let out = audit::render(&q);
    assert!(out.contains("linehaul: 5 cwt @ $22.50/cwt = $112.50 | total $112.50\n"), "got:\n{}", out);
    assert!(out.contains("deficit-weight: rebill at 500 lb, 5 cwt @ $19.10/cwt = $95.50 | total $95.50\n"), "got:\n{}", out);
}

#[test]
fn intra_zone_four_lane_totals() {
    let t = mainline();
    // FLD -> FLD rides lane 4-4 on scale A: 26 cwt @ $11.90 = $309.40,
    // +12.5% fuel $38.68 = $348.08, +4% peak $13.92 = $362.00
    let q = quote(&t, &ship("Q-5150", "FLD", "FLD", 2600, &[], &[])).unwrap();
    assert_eq!(q.total.cents(), 36200);
    let codes: Vec<&str> = q.surcharges.iter().map(|s| s.code.as_str()).collect();
    assert_eq!(codes, vec!["FUEL", "PEAK"]);
}

#[test]
fn fixture_quote_errors() {
    let t = mainline();
    let e = quote(&t, &ship("Q-9", "ALT", "XXX", 100, &[], &[])).unwrap_err();
    assert_eq!(e.to_string(), "unknown station 'XXX'");
    // zones 3 -> 4 has no published lane
    let e = quote(&t, &ship("Q-9", "RVI", "FLD", 100, &[], &[])).unwrap_err();
    assert_eq!(e.to_string(), "no lane from zone 3 to zone 4");
}
