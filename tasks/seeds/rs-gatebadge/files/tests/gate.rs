use rs_gatebadge::id::{BadgeId, Contractor, Visitor};
use rs_gatebadge::registry::GateRegistry;
use rs_gatebadge::render;

#[test]
fn badges_are_issued_sequentially_and_typed() {
    let mut reg = GateRegistry::new();
    let v: BadgeId<Visitor> = reg.sign_in_visitor();
    let c: BadgeId<Contractor> = reg.sign_in_contractor();
    assert_eq!(v.raw(), 100);
    assert_eq!(c.raw(), 101);
    assert_eq!(reg.on_site_count(), 2);
}

#[test]
fn sign_out_retires_exactly_the_signed_in_badge() {
    let mut reg = GateRegistry::new();
    let v = reg.sign_in_visitor();
    let c = reg.sign_in_contractor();
    assert!(reg.sign_out_visitor(v));
    assert!(!reg.sign_out_visitor(BadgeId::new(999)), "never issued");
    assert!(!reg.sign_out_visitor(BadgeId::new(v.raw())), "already left");
    assert!(reg.sign_out_contractor(c));
    assert_eq!(reg.on_site_count(), 0);
}

#[test]
fn badge_ids_are_copy_and_compare_within_a_kind() {
    let a: BadgeId<Visitor> = BadgeId::new(7);
    let b: BadgeId<Visitor> = BadgeId::new(7);
    let copied = a; // Copy must hold: `a` stays usable below
    assert_eq!(a, b);
    assert_eq!(copied.raw(), a.raw());
    assert_ne!(BadgeId::<Contractor>::new(7).raw(), 8);
}

#[test]
fn badge_lines_pad_and_clip_to_the_label_stock() {
    assert_eq!(
        render::badge_line(BadgeId::new(104), "Ada Quist"),
        "[ 104] Ada Quist         "
    );
    assert_eq!(
        render::badge_line(BadgeId::new(9), "Bartholomew Pemberton"),
        "[   9] Bartholomew Pember"
    );
}

#[test]
fn day_header_names_the_gate_and_the_day() {
    assert_eq!(
        render::day_header("North Gate", "2026-07-15"),
        "== North Gate — 2026-07-15 =="
    );
}
