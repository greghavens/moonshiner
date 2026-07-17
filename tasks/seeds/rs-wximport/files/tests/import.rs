use std::error::Error as _;
use std::path::Path;

use rs_wximport::{import_dir, load_station_file, parse_report, ImportSummary, Reading, Report, WxError};

#[test]
fn parses_a_clean_report() {
    let text = "STATION KSEA\n2026-03-01 temp_c=7.5 wind_kt=12\n\n2026-03-02 temp_c=8.0 wind_kt=5\n";
    let report = parse_report(text).expect("clean report should parse");
    assert_eq!(
        report,
        Report {
            station: "KSEA".to_string(),
            readings: vec![
                Reading {
                    date: "2026-03-01".to_string(),
                    temp_c: 7.5,
                    wind_kt: 12.0
                },
                Reading {
                    date: "2026-03-02".to_string(),
                    temp_c: 8.0,
                    wind_kt: 5.0
                },
            ],
        }
    );
}

#[test]
fn error_display_is_pinned() {
    let cases: Vec<(WxError, &str)> = vec![
        (WxError::MissingHeader, "missing STATION header"),
        (
            WxError::BadField {
                line: 3,
                field: "wind_kt=abc".to_string(),
            },
            "line 3: unreadable field \"wind_kt=abc\"",
        ),
        (
            WxError::MissingField {
                line: 2,
                field: "wind_kt".to_string(),
            },
            "line 2: missing field \"wind_kt\"",
        ),
        (
            WxError::OutOfRange {
                line: 2,
                field: "temp_c".to_string(),
                value: 812.0,
            },
            "line 2: temp_c out of range (812)",
        ),
    ];
    for (err, want) in cases {
        assert_eq!(err.to_string(), want);
    }
}

#[test]
fn parse_reports_the_offending_line() {
    let text = "STATION KSEA\n2026-03-01 temp_c=7.5 wind_kt=12\n2026-03-02 temp_c=8.0 wind_kt=abc\n";
    let err = parse_report(text).unwrap_err();
    assert!(matches!(err, WxError::BadField { line: 3, .. }), "got {err:?}");
    assert_eq!(err.to_string(), "line 3: unreadable field \"wind_kt=abc\"");
}

#[test]
fn parse_requires_the_station_header() {
    let err = parse_report("2026-03-01 temp_c=5.0 wind_kt=1\n").unwrap_err();
    assert!(matches!(err, WxError::MissingHeader), "got {err:?}");
}

#[test]
fn parse_requires_both_fields() {
    let err = parse_report("STATION KSEA\n2026-03-01 temp_c=5.0\n").unwrap_err();
    assert!(matches!(err, WxError::MissingField { line: 2, .. }), "got {err:?}");
    assert_eq!(err.to_string(), "line 2: missing field \"wind_kt\"");
}

#[test]
fn parse_enforces_ranges() {
    let err = parse_report("STATION KSEA\n2026-03-01 temp_c=-95.5 wind_kt=1\n").unwrap_err();
    assert!(matches!(err, WxError::OutOfRange { line: 2, .. }), "got {err:?}");
    assert_eq!(err.to_string(), "line 2: temp_c out of range (-95.5)");
}

#[test]
fn read_failures_keep_the_io_source() {
    let err = load_station_file(Path::new("tests/fixtures/nope.wx")).unwrap_err();
    assert_eq!(err.to_string(), "could not read tests/fixtures/nope.wx");
    let source = err.source().expect("io cause should be chained");
    let io = source
        .downcast_ref::<std::io::Error>()
        .expect("source should be std::io::Error");
    assert_eq!(io.kind(), std::io::ErrorKind::NotFound);
}

#[test]
fn import_dir_counts_files_and_readings() {
    let summary = import_dir(Path::new("tests/fixtures/goodset")).expect("goodset imports");
    assert_eq!(
        summary,
        ImportSummary {
            files: 2,
            readings: 5
        }
    );
}

#[test]
fn import_dir_wraps_parse_errors_with_file_context() {
    let err = import_dir(Path::new("tests/fixtures/badset")).unwrap_err();
    assert_eq!(err.to_string(), "importing cedar.wx");
    assert_eq!(
        format!("{err:#}"),
        "importing cedar.wx: line 2: temp_c out of range (812)"
    );
    assert_eq!(err.chain().count(), 2);
    let wx = err
        .downcast_ref::<WxError>()
        .expect("original WxError should survive the context wrap");
    assert!(matches!(wx, WxError::OutOfRange { line: 2, .. }), "got {wx:?}");
    assert_eq!(
        err.root_cause().to_string(),
        "line 2: temp_c out of range (812)"
    );
}

#[test]
fn import_dir_rejects_a_directory_with_no_station_files() {
    let err = import_dir(Path::new("tests/fixtures/emptyset")).unwrap_err();
    assert_eq!(err.to_string(), "no .wx files in tests/fixtures/emptyset");
    assert!(
        err.downcast_ref::<WxError>().is_none(),
        "empty dir is an app-level error, not a WxError"
    );
}
