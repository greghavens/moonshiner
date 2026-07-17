//! Ingest pipeline contract. The first section pins behavior that has
//! always worked in production; the second section encodes this week's field
//! reports as regressions. Epochs are precomputed UTC values.

use rs_logingest::{ingest, IngestConfig, ParseError};

fn cfg(default_offset_minutes: i32, batch_size: usize) -> IngestConfig {
    IngestConfig {
        default_offset_minutes,
        batch_size,
    }
}

// ---------------------------------------------------------------------------
// Healthy paths — these pass today and must keep passing.
// ---------------------------------------------------------------------------

#[test]
fn explicit_offset_records_normalize_to_utc() {
    // 2026-03-14T22:10:05+01:00 == 2026-03-14T21:10:05Z == 1773522605.
    let input = "2026-03-14T22:10:05+01:00 host=web-1 level=error msg=\"db timeout\"\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!(out.records, vec!["1773522605 web-1 error db timeout"]);
    assert_eq!((out.accepted, out.duplicates), (1, 0));
}

#[test]
fn zulu_timestamps_are_already_utc() {
    let input = "2026-03-14T21:10:05Z host=web-1 level=error msg=\"db timeout\"\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!(out.records, vec!["1773522605 web-1 error db timeout"]);
}

#[test]
fn explicit_offsets_agree_across_zones() {
    // Three spellings of 2026-03-14T10:00:00Z (== 1773482400).
    let input = "2026-03-14T10:00:00Z host=a level=info msg=\"m1\"\n\
                 2026-03-14T11:00:00+01:00 host=a level=info msg=\"m2\"\n\
                 2026-03-14T06:00:00-04:00 host=a level=info msg=\"m3\"\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!(out.records.len(), 3);
    for rec in &out.records {
        assert!(
            rec.starts_with("1773482400 "),
            "expected UTC epoch 1773482400, got: {rec}"
        );
    }
}

#[test]
fn exact_duplicates_from_one_host_collapse() {
    let line = "2026-03-14T21:10:05Z host=web-1 level=error msg=\"db timeout\"\n";
    let input = format!("{line}{line}");
    let out = ingest(&input, &cfg(0, 1)).unwrap();
    assert_eq!((out.accepted, out.duplicates), (1, 1));
    assert_eq!(out.records.len(), 1);
}

#[test]
fn full_batches_flush_in_input_order() {
    let input = "2026-03-14T10:00:00Z host=a level=info msg=\"e1\"\n\
                 2026-03-14T10:00:01Z host=a level=info msg=\"e2\"\n\
                 2026-03-14T10:00:02Z host=a level=info msg=\"e3\"\n\
                 2026-03-14T10:00:03Z host=a level=info msg=\"e4\"\n";
    let out = ingest(input, &cfg(0, 2)).unwrap();
    assert_eq!(
        out.records,
        vec![
            "1773482400 a info e1",
            "1773482401 a info e2",
            "1773482402 a info e3",
            "1773482403 a info e4",
        ]
    );
}

#[test]
fn parse_errors_carry_the_line_number() {
    let input = "2026-03-14T10:00:00Z host=a level=info msg=\"ok\"\n\
                 2026-03-14T10:00:01Z host=a level=info msg=\"ok2\"\n\
                 2026-03-14T10:00:02Z host=a msg=\"level is missing\"\n";
    let err = ingest(input, &cfg(0, 1)).unwrap_err();
    assert_eq!(
        err,
        ParseError {
            line: 3,
            msg: "missing field 'level'".to_string()
        }
    );
    assert_eq!(err.to_string(), "line 3: missing field 'level'");
}

#[test]
fn bad_timestamps_are_rejected() {
    let input = "2026-13-01T00:00:00Z host=a level=info msg=\"x\"\n";
    let err = ingest(input, &cfg(0, 1)).unwrap_err();
    assert_eq!(
        err,
        ParseError {
            line: 1,
            msg: "bad timestamp".to_string()
        }
    );
}

#[test]
fn blank_lines_are_skipped() {
    let input = "\n2026-03-14T10:00:00Z host=a level=info msg=\"e1\"\n\n\n\
                 2026-03-14T10:00:01Z host=a level=info msg=\"e2\"\n\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!((out.accepted, out.duplicates), (2, 0));
    assert_eq!(out.records.len(), 2);
}

// ---------------------------------------------------------------------------
// Field reports — regressions to root-cause and fix.
// ---------------------------------------------------------------------------

#[test]
fn offsetless_timestamps_honor_the_default_offset() {
    // Fleet at +02:00 (120 minutes): local 10:00 is 08:00 UTC == 1773475200.
    let input = "2026-03-14T10:00:00 host=web-1 level=info msg=\"tick\"\n";
    let out = ingest(input, &cfg(120, 1)).unwrap();
    assert_eq!(out.records, vec!["1773475200 web-1 info tick"]);
}

#[test]
fn offsetless_midnight_events_land_on_the_previous_utc_day() {
    // Fleet at +01:00: local 2026-01-02T00:30 is 2026-01-01T23:30Z == 1767310200.
    let input = "2026-01-02T00:30:00 host=web-1 level=info msg=\"rollover\"\n";
    let out = ingest(input, &cfg(60, 1)).unwrap();
    assert_eq!(out.records, vec!["1767310200 web-1 info rollover"]);
}

#[test]
fn same_text_from_two_hosts_is_two_events() {
    let input = "2026-03-14T21:10:05Z host=web-1 level=error msg=\"disk full\"\n\
                 2026-03-14T21:10:05Z host=web-2 level=error msg=\"disk full\"\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!((out.accepted, out.duplicates), (2, 0));
    assert_eq!(
        out.records,
        vec![
            "1773522605 web-1 error disk full",
            "1773522605 web-2 error disk full",
        ]
    );
}

#[test]
fn per_host_dedup_still_collapses_true_repeats() {
    let input = "2026-03-14T21:10:05Z host=web-1 level=error msg=\"disk full\"\n\
                 2026-03-14T21:10:05Z host=web-1 level=error msg=\"disk full\"\n\
                 2026-03-14T21:10:05Z host=web-2 level=error msg=\"disk full\"\n";
    let out = ingest(input, &cfg(0, 1)).unwrap();
    assert_eq!((out.accepted, out.duplicates), (2, 1));
}

#[test]
fn final_partial_batch_is_not_lost() {
    let input = "2026-03-14T10:00:00Z host=a level=info msg=\"e1\"\n\
                 2026-03-14T10:00:01Z host=a level=info msg=\"e2\"\n\
                 2026-03-14T10:00:02Z host=a level=info msg=\"e3\"\n\
                 2026-03-14T10:00:03Z host=a level=info msg=\"e4\"\n\
                 2026-03-14T10:00:04Z host=a level=info msg=\"e5\"\n";
    let out = ingest(input, &cfg(0, 2)).unwrap();
    assert_eq!(out.records.len(), 5, "the tail of the run must survive");
    assert_eq!(out.records[4], "1773482404 a info e5");
}

#[test]
fn runs_smaller_than_one_batch_still_produce_output() {
    let input = "2026-03-14T10:00:00Z host=a level=info msg=\"only\"\n";
    let out = ingest(input, &cfg(0, 10)).unwrap();
    assert_eq!(out.records, vec!["1773482400 a info only"]);
}

#[test]
fn accepted_count_matches_emitted_records() {
    let mut input = String::new();
    for i in 0..7 {
        input.push_str(&format!(
            "2026-03-14T10:00:0{i}Z host=a level=info msg=\"e{i}\"\n"
        ));
    }
    let out = ingest(&input, &cfg(0, 3)).unwrap();
    assert_eq!(out.accepted, 7);
    assert_eq!(
        out.records.len(),
        out.accepted,
        "every accepted record must be emitted"
    );
}

#[test]
fn end_to_end_run_matches_the_operator_report() {
    // Fleet at +01:00, batches of 2. Mixed offset styles, a cross-host
    // repeat of the same alert text, one true duplicate, and a partial tail.
    let input = "2026-03-14T10:00:00Z host=web-1 level=info msg=\"boot\"\n\
                 2026-03-14T11:00:00 host=web-1 level=error msg=\"disk full\"\n\
                 2026-03-14T11:00:00+01:00 host=web-2 level=error msg=\"disk full\"\n\
                 2026-03-14T12:00:00Z host=web-1 level=info msg=\"heartbeat\"\n\
                 2026-03-14T11:00:00+01:00 host=web-2 level=error msg=\"disk full\"\n\
                 2026-03-14T13:00:00Z host=web-3 level=warn msg=\"slow io\"\n";
    let out = ingest(input, &cfg(60, 2)).unwrap();
    assert_eq!((out.accepted, out.duplicates), (5, 1));
    assert_eq!(
        out.records,
        vec![
            "1773482400 web-1 info boot",
            "1773482400 web-1 error disk full",
            "1773482400 web-2 error disk full",
            "1773489600 web-1 info heartbeat",
            "1773493200 web-3 warn slow io",
        ]
    );
}
