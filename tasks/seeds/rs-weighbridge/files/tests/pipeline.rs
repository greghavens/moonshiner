//! End-to-end contract for the weighbridge export pipeline.
//!
//! Every expected figure below was recomputed by hand from the raw ticket
//! values (lb -> kg at 0.45359237, totals to the nearest 0.01 t, halves up).

use rs_weighbridge::report::ReportLine;
use rs_weighbridge::window::WindowError;
use rs_weighbridge::{process, render_report, PipelineError};

fn line(start_minute: u32, material: &str, centi_tonnes: i64) -> ReportLine {
    ReportLine {
        start_minute,
        material: material.to_string(),
        centi_tonnes,
    }
}

#[test]
fn new_lane_kg_ticket_exact_total() {
    let got = process(&["S2|400|TRK-101|gravel|12400|kg"]).unwrap();
    assert_eq!(got, vec![line(360, "gravel", 1240)]);
}

#[test]
fn new_lane_lb_ticket_converts_once() {
    // 24800 lb * 0.45359237 = 11249.090776 kg -> 11.25 t
    let got = process(&["S2|400|TRK-102|gravel|24800|lb"]).unwrap();
    assert_eq!(got, vec![line(360, "gravel", 1125)]);
}

#[test]
fn legacy_lane_lb_ticket_matches_new_lane() {
    // The very same trailer over the legacy lane must weigh the same:
    // 24800 lb -> 11.25 t, whichever head punched the ticket.
    let got = process(&["S1|400|TRK-102|gravel|24800|lb"]).unwrap();
    assert_eq!(got, vec![line(360, "gravel", 1125)]);
}

#[test]
fn shift_change_ticket_credited_to_new_shift() {
    // Day shift is [06:00, 14:00); a ticket punched at exactly 14:00
    // (minute 840) belongs to the swing shift.
    let got = process(&[
        "S2|839|TRK-210|gravel|8205|kg",
        "S2|840|TRK-330|gravel|12405|kg",
    ])
    .unwrap();
    assert_eq!(
        got,
        vec![line(360, "gravel", 821), line(840, "gravel", 1241)]
    );
}

#[test]
fn first_ticket_exactly_at_shift_change() {
    let got = process(&["S2|840|TRK-330|gravel|12405|kg"]).unwrap();
    assert_eq!(got, vec![line(840, "gravel", 1241)]);
}

#[test]
fn totals_round_to_nearest_ten_kg() {
    // 4101 kg + 8302 kg = 12403 kg = 12.403 t -> 12.40 t
    let got = process(&[
        "S2|400|TRK-118|gravel|4101|kg",
        "S2|410|TRK-119|gravel|8302|kg",
    ])
    .unwrap();
    assert_eq!(got, vec![line(360, "gravel", 1240)]);
}

#[test]
fn totals_round_halves_up() {
    // 12405 kg = 12.405 t -> 12.41 t
    let got = process(&["S2|400|TRK-118|gravel|12405|kg"]).unwrap();
    assert_eq!(got, vec![line(360, "gravel", 1241)]);
}

#[test]
fn materials_reported_in_sorted_order() {
    let got = process(&[
        "S2|400|TRK-118|sand|700|kg",
        "S2|410|TRK-119|gravel|12400|kg",
        "S2|420|TRK-120|basecourse|5000|kg",
    ])
    .unwrap();
    assert_eq!(
        got,
        vec![
            line(360, "basecourse", 500),
            line(360, "gravel", 1240),
            line(360, "sand", 70),
        ]
    );
}

#[test]
fn render_formats_shift_and_tonnes() {
    let got = render_report(&["S2|372|TRK-118|sand|8205|kg"]).unwrap();
    assert_eq!(got, "06:00 sand 8.21t\n");
}

#[test]
fn day_report_end_to_end() {
    let got = render_report(&[
        "S2|371|TRK-201|gravel|14000|kg",
        "S1|405|TRK-104|gravel|24800|lb",
        "S2|512|TRK-201|sand|9103|kg",
        "S2|840|TRK-330|gravel|12405|kg",
        "S2|1004|TRK-104|sand|7300|kg",
    ])
    .unwrap();
    // Day shift gravel: 14000 kg + 11249.090776 kg = 25249.090776 -> 25.25 t
    // Day shift sand:   9103 kg -> 9.10 t
    // Swing gravel:     12405 kg (punched at 14:00 sharp) -> 12.41 t
    // Swing sand:       7300 kg -> 7.30 t
    assert_eq!(
        got,
        "06:00 gravel 25.25t\n06:00 sand 9.10t\n14:00 gravel 12.41t\n14:00 sand 7.30t\n"
    );
}

#[test]
fn rejects_malformed_field_count() {
    let err = process(&["S2|400|TRK-1|gravel|1000"]).unwrap_err();
    assert!(matches!(
        err,
        PipelineError::Ticket(rs_weighbridge::ingest::TicketError::FieldCount(5))
    ));
}

#[test]
fn rejects_unknown_unit_and_head() {
    let err = process(&["S2|400|TRK-1|gravel|1000|st"]).unwrap_err();
    assert!(matches!(
        err,
        PipelineError::Ticket(rs_weighbridge::ingest::TicketError::BadUnit(_))
    ));
    let err = process(&["S9|400|TRK-1|gravel|1000|kg"]).unwrap_err();
    assert!(matches!(
        err,
        PipelineError::Ticket(rs_weighbridge::ingest::TicketError::UnknownHead(_))
    ));
}

#[test]
fn rejects_out_of_order_export() {
    let err = process(&[
        "S2|500|TRK-1|gravel|1000|kg",
        "S2|480|TRK-2|gravel|1000|kg",
    ])
    .unwrap_err();
    assert!(matches!(
        err,
        PipelineError::Window(WindowError::OutOfOrder { prev: 500, got: 480 })
    ));
}

#[test]
fn rejects_ticket_before_opening() {
    let err = process(&["S2|300|TRK-1|gravel|1000|kg"]).unwrap_err();
    assert!(matches!(
        err,
        PipelineError::Window(WindowError::BeforeOpen { open: 360, got: 300 })
    ));
}
