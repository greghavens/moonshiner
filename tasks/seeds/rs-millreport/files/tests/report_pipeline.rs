//! End-to-end contract for the shift report pipeline (live mode).

use rs_millreport::{run, Config};

const FEED: &str = concat!(
    "# hot mill, morning pull\n",
    "stand-1,produced,120\n",
    "stand-1,downtime_min,35\n",
    "stand-2,produced,98\n",
    "stand-2,downtime_min,52\n",
    "stand-3,produced,143\n",
    "stand-3,scrap,7\n",
);

const CSV_EXPECTED: &str = concat!(
    "machine,produced,downtime_min,scrap\n",
    "stand-1,120,35,0\n",
    "stand-2,98,52,0\n",
    "stand-3,143,0,7\n",
);

const DIGEST_EXPECTED: &str = concat!(
    "SHIFT REPORT: day\n",
    "machine    produced  downtime  scrap\n",
    "stand-1         120        35      0\n",
    "stand-2          98        52      0\n",
    "stand-3         143         0      7\n",
    "machines: 3\n",
);

fn strings(items: &[&str]) -> Vec<String> {
    items.iter().map(|s| s.to_string()).collect()
}

#[test]
fn config_defaults() {
    let cfg = Config::parse(&[]).expect("empty args parse");
    assert_eq!(cfg.shift, "day");
    assert_eq!(cfg.writers, None);
    assert_eq!(cfg.summaries, None);
}

#[test]
fn config_flags_parse() {
    let cfg = Config::parse(&[
        "--shift",
        "night",
        "--writers",
        "csv",
        "--summaries",
        "totals,alerts",
    ])
    .expect("flags parse");
    assert_eq!(cfg.shift, "night");
    assert_eq!(cfg.writers, Some(strings(&["csv"])));
    assert_eq!(cfg.summaries, Some(strings(&["totals", "alerts"])));
}

#[test]
fn config_rejects_unknown_flag() {
    let err = Config::parse(&["--frobnicate"]).unwrap_err();
    assert_eq!(err.to_string(), "unknown flag: --frobnicate");
}

#[test]
fn config_missing_duplicate_and_empty() {
    let err = Config::parse(&["--shift"]).unwrap_err();
    assert_eq!(err.to_string(), "missing value for --shift");
    let err = Config::parse(&["--shift", "a", "--shift", "b"]).unwrap_err();
    assert_eq!(err.to_string(), "duplicate flag: --shift");
    let err = Config::parse(&["--writers", ""]).unwrap_err();
    assert_eq!(err.to_string(), "empty list for --writers");
}

#[test]
fn feed_parse_errors_carry_line_numbers() {
    let err = run(&[], "stand-1,produced\n").unwrap_err();
    assert_eq!(err.to_string(), "line 1: expected machine,metric,value");
    let err = run(&[], "# ok\nstand-1,power,3\n").unwrap_err();
    assert_eq!(err.to_string(), "line 2: unknown metric power");
    let err = run(&[], "stand-1,produced,twelve\n").unwrap_err();
    assert_eq!(err.to_string(), "line 1: bad value twelve");
}

#[test]
fn full_run_renders_files_exactly() {
    let outcome = run(&[], FEED).expect("full run");
    let names: Vec<&str> = outcome.files.keys().map(|k| k.as_str()).collect();
    assert_eq!(names, ["machine_totals.csv", "shift_digest_day.txt"]);
    assert_eq!(outcome.files["machine_totals.csv"], CSV_EXPECTED);
    assert_eq!(outcome.files["shift_digest_day.txt"], DIGEST_EXPECTED);
}

#[test]
fn full_run_summary_exact() {
    let outcome = run(&[], FEED).expect("full run");
    assert_eq!(
        outcome.summary,
        strings(&[
            "machines: 3",
            "produced total: 361",
            "downtime total: 87 min",
            "scrap total: 7",
            "alert: stand-2 downtime 52 min",
        ])
    );
}

#[test]
fn writer_subset_runs_only_selected() {
    let outcome = run(&["--writers", "csv"], FEED).expect("csv only");
    let names: Vec<&str> = outcome.files.keys().map(|k| k.as_str()).collect();
    assert_eq!(names, ["machine_totals.csv"]);
    assert_eq!(outcome.files["machine_totals.csv"], CSV_EXPECTED);
}

#[test]
fn summary_selection_keeps_registry_order() {
    let outcome = run(&["--summaries", "alerts,totals"], FEED).expect("both summaries");
    assert_eq!(
        outcome.summary,
        strings(&[
            "machines: 3",
            "produced total: 361",
            "downtime total: 87 min",
            "scrap total: 7",
            "alert: stand-2 downtime 52 min",
        ])
    );
    let outcome = run(&["--summaries", "alerts"], FEED).expect("alerts only");
    assert_eq!(outcome.summary, strings(&["alert: stand-2 downtime 52 min"]));
}

#[test]
fn unknown_plugin_names_rejected() {
    let err = run(&["--writers", "punchcard"], FEED).unwrap_err();
    assert_eq!(err.to_string(), "unknown writer: punchcard");
    let err = run(&["--summaries", "vibes"], FEED).unwrap_err();
    assert_eq!(err.to_string(), "unknown summary: vibes");
}

#[test]
fn empty_feed_still_reports() {
    let outcome = run(&[], "").expect("empty feed");
    assert_eq!(
        outcome.files["machine_totals.csv"],
        "machine,produced,downtime_min,scrap\n"
    );
    assert_eq!(
        outcome.files["shift_digest_day.txt"],
        concat!(
            "SHIFT REPORT: day\n",
            "machine    produced  downtime  scrap\n",
            "machines: 0\n",
        )
    );
    assert_eq!(
        outcome.summary,
        strings(&[
            "machines: 0",
            "produced total: 0",
            "downtime total: 0 min",
            "scrap total: 0",
            "alerts: none",
        ])
    );
}

#[test]
fn shift_label_threads_through() {
    let outcome = run(&["--shift", "night"], FEED).expect("night shift");
    let digest = &outcome.files["shift_digest_night.txt"];
    assert!(digest.starts_with("SHIFT REPORT: night\n"));
}
