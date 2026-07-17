use rs_nansort::{build_report, sort_rows, worst_station, ReportRow, StationWindow};

fn row(station: &str, rate: f64) -> ReportRow {
    ReportRow {
        station: station.to_string(),
        rate,
    }
}

#[test]
fn rate_is_the_rejected_fraction() {
    assert_eq!(StationWindow::new("weld-cell", 4, 1).reject_rate(), 0.25);
    assert_eq!(StationWindow::new("paint-booth", 8, 4).reject_rate(), 0.5);
}

#[test]
fn clean_station_has_zero_rate() {
    assert_eq!(StationWindow::new("kitting", 50, 0).reject_rate(), 0.0);
}

#[test]
fn everything_rejected_is_rate_one() {
    assert_eq!(StationWindow::new("anodize", 8, 8).reject_rate(), 1.0);
}

#[test]
fn idle_station_counts_as_zero_rate() {
    // A station that inspected nothing in the window is not a defect: 0.0.
    assert_eq!(StationWindow::new("paint-booth", 0, 0).reject_rate(), 0.0);
}

#[test]
fn report_orders_worst_first() {
    let windows = vec![
        StationWindow::new("kitting", 8, 1),     // 0.125
        StationWindow::new("weld-cell", 4, 3),   // 0.75
        StationWindow::new("paint-booth", 8, 2), // 0.25
    ];
    assert_eq!(
        build_report(&windows),
        vec![
            row("weld-cell", 0.75),
            row("paint-booth", 0.25),
            row("kitting", 0.125),
        ]
    );
}

#[test]
fn equal_rates_are_alphabetical() {
    let windows = vec![
        StationWindow::new("weld-cell", 8, 2),
        StationWindow::new("anodize", 4, 1),
        StationWindow::new("kitting", 16, 4),
    ];
    assert_eq!(
        build_report(&windows),
        vec![row("anodize", 0.25), row("kitting", 0.25), row("weld-cell", 0.25)]
    );
}

#[test]
fn report_survives_an_idle_station() {
    // Monday morning after the paint line sat idle all weekend.
    let windows = vec![
        StationWindow::new("weld-cell", 4, 1),
        StationWindow::new("paint-booth", 0, 0),
        StationWindow::new("kitting", 8, 0),
        StationWindow::new("final-assy", 2, 1),
    ];
    assert_eq!(
        build_report(&windows),
        vec![
            row("final-assy", 0.5),
            row("weld-cell", 0.25),
            row("kitting", 0.0),
            row("paint-booth", 0.0),
        ]
    );
}

#[test]
fn holiday_week_all_stations_idle() {
    let windows = vec![
        StationWindow::new("weld-cell", 0, 0),
        StationWindow::new("anodize", 0, 0),
        StationWindow::new("kitting", 0, 0),
    ];
    assert_eq!(
        build_report(&windows),
        vec![row("anodize", 0.0), row("kitting", 0.0), row("weld-cell", 0.0)]
    );
}

#[test]
fn worst_station_picks_the_highest_rate() {
    let windows = vec![
        StationWindow::new("kitting", 8, 1),
        StationWindow::new("anodize", 8, 6),
        StationWindow::new("weld-cell", 8, 2),
    ];
    assert_eq!(worst_station(&windows), Some("anodize".to_string()));
}

#[test]
fn worst_station_ignores_idle_neighbours() {
    let windows = vec![
        StationWindow::new("anneal", 0, 0),
        StationWindow::new("buffing", 8, 2),
    ];
    assert_eq!(worst_station(&windows), Some("buffing".to_string()));
}

#[test]
fn worst_station_of_nothing_is_none() {
    assert_eq!(worst_station(&[]), None);
}

#[test]
fn snapshot_rows_with_nan_rates_sort_last() {
    // Rows re-loaded from an old snapshot file; one carries a junk rate.
    let mut rows = vec![
        row("lift-3", f64::NAN),
        row("axle", 0.25),
        row("bevel", 0.125),
    ];
    sort_rows(&mut rows);
    assert_eq!(rows[0], row("axle", 0.25));
    assert_eq!(rows[1], row("bevel", 0.125));
    assert_eq!(rows[2].station, "lift-3");
    assert!(rows[2].rate.is_nan());
}

#[test]
fn multiple_junk_rows_sort_last_alphabetically() {
    let mut rows = vec![
        row("rivet", f64::NAN),
        row("gasket", 0.5),
        row("anneal", f64::NAN),
        row("deburr", 0.0),
    ];
    sort_rows(&mut rows);
    let order: Vec<&str> = rows.iter().map(|r| r.station.as_str()).collect();
    assert_eq!(order, vec!["gasket", "deburr", "anneal", "rivet"]);
    assert!(rows[2].rate.is_nan());
    assert!(rows[3].rate.is_nan());
}

#[test]
fn sorting_nothing_or_one_row_is_fine() {
    let mut empty: Vec<ReportRow> = vec![];
    sort_rows(&mut empty);
    assert!(empty.is_empty());

    let mut one = vec![row("kitting", 0.125)];
    sort_rows(&mut one);
    assert_eq!(one, vec![row("kitting", 0.125)]);
}

#[test]
fn sort_rows_orders_desc_with_name_ties() {
    let mut rows = vec![
        row("weld-cell", 0.25),
        row("anodize", 0.75),
        row("kitting", 0.25),
    ];
    sort_rows(&mut rows);
    assert_eq!(
        rows,
        vec![row("anodize", 0.75), row("kitting", 0.25), row("weld-cell", 0.25)]
    );
}
