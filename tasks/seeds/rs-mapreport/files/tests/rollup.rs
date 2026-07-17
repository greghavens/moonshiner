use rs_mapreport::{fmt_cents, Rollup};

/// June's ledger entries, in the order the importer posts them.
/// 29 entries across 26 cost centers (three centers have split postings).
const JUNE_LEDGER: [(&str, i64); 29] = [
    ("mkt-events", 30000),
    ("eng-sre", 121212),
    ("sales-na", 245600),
    ("fin-tax", 42042),
    ("eng-api", 100000),
    ("ops-facilities", 310000),
    ("hr-hiring", 92500),
    ("legal-ip", 88250),
    ("eng-platform", 200000),
    ("support-tier1", 58900),
    ("it-licenses", 275099),
    ("eng-mobile", 66400),
    ("fin-payroll", 350075),
    ("sales-apac", 175800),
    ("eng-qa", 45000),
    ("capex-fleet", 250000),
    ("hr-benefits", 188800),
    ("mkt-events", -42550),
    ("eng-build", 87300),
    ("legal-contracts", 120000),
    ("fin-audit", 78000),
    ("it-helpdesk", 31000),
    ("eng-security", 99999),
    ("sales-emea", 201300),
    ("eng-data", 152000),
    ("mkt-brand", 140500),
    ("eng-api", 4250),
    ("ops-cleaning", 27500),
    ("eng-platform", 3150),
];

/// The June totals in cost-center code order — the runbook contract.
const JUNE_ROWS: [(&str, i64); 26] = [
    ("capex-fleet", 250000),
    ("eng-api", 104250),
    ("eng-build", 87300),
    ("eng-data", 152000),
    ("eng-mobile", 66400),
    ("eng-platform", 203150),
    ("eng-qa", 45000),
    ("eng-security", 99999),
    ("eng-sre", 121212),
    ("fin-audit", 78000),
    ("fin-payroll", 350075),
    ("fin-tax", 42042),
    ("hr-benefits", 188800),
    ("hr-hiring", 92500),
    ("it-helpdesk", 31000),
    ("it-licenses", 275099),
    ("legal-contracts", 120000),
    ("legal-ip", 88250),
    ("mkt-brand", 140500),
    ("mkt-events", -12550),
    ("ops-cleaning", 27500),
    ("ops-facilities", 310000),
    ("sales-apac", 175800),
    ("sales-emea", 201300),
    ("sales-na", 245600),
    ("support-tier1", 58900),
];

const JUNE_REPORT: &str = "capex-fleet  2500.00\n\
eng-api  1042.50\n\
eng-build  873.00\n\
eng-data  1520.00\n\
eng-mobile  664.00\n\
eng-platform  2031.50\n\
eng-qa  450.00\n\
eng-security  999.99\n\
eng-sre  1212.12\n\
fin-audit  780.00\n\
fin-payroll  3500.75\n\
fin-tax  420.42\n\
hr-benefits  1888.00\n\
hr-hiring  925.00\n\
it-helpdesk  310.00\n\
it-licenses  2750.99\n\
legal-contracts  1200.00\n\
legal-ip  882.50\n\
mkt-brand  1405.00\n\
mkt-events  -125.50\n\
ops-cleaning  275.00\n\
ops-facilities  3100.00\n\
sales-apac  1758.00\n\
sales-emea  2013.00\n\
sales-na  2456.00\n\
support-tier1  589.00\n\
TOTAL  35421.27\n";

fn june_rollup() -> Rollup {
    let mut rollup = Rollup::new();
    for (center, cents) in JUNE_LEDGER {
        rollup.add(center, cents);
    }
    rollup
}

fn expected_rows() -> Vec<(String, i64)> {
    JUNE_ROWS
        .iter()
        .map(|(center, cents)| (center.to_string(), *cents))
        .collect()
}

#[test]
fn add_accumulates_per_center() {
    let mut rollup = Rollup::new();
    rollup.add("eng-api", 1500);
    rollup.add("eng-api", 250);
    assert_eq!(rollup.center_total("eng-api"), Some(1750));
}

#[test]
fn unknown_center_has_no_total() {
    let rollup = june_rollup();
    assert_eq!(rollup.center_total("eng-nonexistent"), None);
}

#[test]
fn total_cents_sums_every_center() {
    let mut rollup = Rollup::new();
    rollup.add("fin-audit", 1000);
    rollup.add("hr-hiring", 2500);
    rollup.add("fin-audit", 40);
    assert_eq!(rollup.total_cents(), 3540);
}

#[test]
fn fmt_cents_pads_to_two_places() {
    assert_eq!(fmt_cents(31000), "310.00");
    assert_eq!(fmt_cents(99999), "999.99");
    assert_eq!(fmt_cents(5), "0.05");
}

#[test]
fn fmt_cents_handles_refund_amounts() {
    assert_eq!(fmt_cents(-12550), "-125.50");
    assert_eq!(fmt_cents(-7), "-0.07");
}

#[test]
fn fmt_cents_zero() {
    assert_eq!(fmt_cents(0), "0.00");
}

#[test]
fn refunds_can_take_a_center_net_negative() {
    let rollup = june_rollup();
    assert_eq!(rollup.center_total("mkt-events"), Some(-12550));
}

#[test]
fn single_center_report() {
    let mut rollup = Rollup::new();
    rollup.add("ops-cleaning", 27500);
    assert_eq!(rollup.rows(), vec![("ops-cleaning".to_string(), 27500)]);
    assert_eq!(rollup.render(), "ops-cleaning  275.00\nTOTAL  275.00\n");
}

#[test]
fn empty_rollup_renders_total_only() {
    let rollup = Rollup::new();
    assert_eq!(rollup.rows(), vec![]);
    assert_eq!(rollup.render(), "TOTAL  0.00\n");
}

#[test]
fn rows_come_out_in_code_order() {
    let rollup = june_rollup();
    assert_eq!(rollup.rows(), expected_rows());
}

#[test]
fn render_matches_the_reconciliation_fixture() {
    let rollup = june_rollup();
    assert_eq!(rollup.render(), JUNE_REPORT);
}

#[test]
fn identical_ledgers_render_identically() {
    // The reconciliation tool diffs tonight's report against last night's:
    // the same ledger must produce byte-identical output every time.
    let first = june_rollup();
    let second = june_rollup();
    assert_eq!(first.rows(), second.rows());
    assert_eq!(first.render(), second.render());
}

#[test]
fn posting_order_does_not_leak_into_the_report() {
    let forward = june_rollup();
    let mut reversed = Rollup::new();
    for (center, cents) in JUNE_LEDGER.iter().rev() {
        reversed.add(center, *cents);
    }
    assert_eq!(forward.rows(), reversed.rows());
    assert_eq!(reversed.rows(), expected_rows());
}
