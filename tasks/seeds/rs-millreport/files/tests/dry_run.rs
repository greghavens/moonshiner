//! Acceptance contract for dry-run mode.
//!
//! `--dry-run` must thread through config, pipeline, writers and summary
//! plugins: nothing is rendered into the file map, the summary opens with
//! the dry-run banner, then one plan line per selected writer (registry
//! order) sized against the exact bytes the writer would have produced,
//! then every summary line prefixed with `[dry-run] `. Live mode must stay
//! byte-identical to the pre-feature behavior.

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
fn dry_run_flag_accepted() {
    let cfg = Config::parse(&["--dry-run"]);
    assert!(cfg.is_ok(), "--dry-run must parse, got {cfg:?}");
    let err = Config::parse(&["--dry-rum"]).unwrap_err();
    assert_eq!(err.to_string(), "unknown flag: --dry-rum");
}

#[test]
fn dry_run_writes_no_files() {
    let outcome = run(&["--dry-run"], FEED).expect("dry run");
    assert!(
        outcome.files.is_empty(),
        "dry run must not render files, got {:?}",
        outcome.files.keys().collect::<Vec<_>>()
    );
}

#[test]
fn dry_run_summary_exact() {
    let outcome = run(&["--dry-run"], FEED).expect("dry run");
    assert_eq!(
        outcome.summary,
        strings(&[
            "DRY RUN - no files written",
            "plan: machine_totals.csv (85 bytes, 4 lines)",
            "plan: shift_digest_day.txt (178 bytes, 6 lines)",
            "[dry-run] machines: 3",
            "[dry-run] produced total: 361",
            "[dry-run] downtime total: 87 min",
            "[dry-run] scrap total: 7",
            "[dry-run] alert: stand-2 downtime 52 min",
        ])
    );
}

#[test]
fn dry_run_respects_writer_selection() {
    let outcome = run(&["--dry-run", "--writers", "csv"], FEED).expect("dry run, csv only");
    assert!(outcome.files.is_empty());
    assert_eq!(
        outcome.summary,
        strings(&[
            "DRY RUN - no files written",
            "plan: machine_totals.csv (85 bytes, 4 lines)",
            "[dry-run] machines: 3",
            "[dry-run] produced total: 361",
            "[dry-run] downtime total: 87 min",
            "[dry-run] scrap total: 7",
            "[dry-run] alert: stand-2 downtime 52 min",
        ])
    );
}

#[test]
fn dry_run_plans_the_shift_labelled_digest() {
    let outcome = run(&["--dry-run", "--shift", "night"], FEED).expect("night dry run");
    assert_eq!(
        outcome.summary[2],
        "plan: shift_digest_night.txt (180 bytes, 6 lines)"
    );
}

#[test]
fn live_mode_stays_byte_identical() {
    let outcome = run(&[], FEED).expect("live run");
    let names: Vec<&str> = outcome.files.keys().map(|k| k.as_str()).collect();
    assert_eq!(names, ["machine_totals.csv", "shift_digest_day.txt"]);
    assert_eq!(outcome.files["machine_totals.csv"], CSV_EXPECTED);
    assert_eq!(outcome.files["shift_digest_day.txt"], DIGEST_EXPECTED);
    assert_eq!(outcome.summary[0], "machines: 3");
    assert!(outcome
        .summary
        .iter()
        .all(|line| !line.contains("dry-run") && !line.contains("DRY RUN")));
}

#[test]
fn dry_run_on_empty_feed() {
    let outcome = run(&["--dry-run"], "").expect("empty dry run");
    assert!(outcome.files.is_empty());
    assert_eq!(
        outcome.summary,
        strings(&[
            "DRY RUN - no files written",
            "plan: machine_totals.csv (36 bytes, 1 lines)",
            "plan: shift_digest_day.txt (67 bytes, 3 lines)",
            "[dry-run] machines: 0",
            "[dry-run] produced total: 0",
            "[dry-run] downtime total: 0 min",
            "[dry-run] scrap total: 0",
            "[dry-run] alerts: none",
        ])
    );
}

#[test]
fn dry_run_still_validates_plugin_names() {
    let err = run(&["--dry-run", "--writers", "punchcard"], FEED).unwrap_err();
    assert_eq!(err.to_string(), "unknown writer: punchcard");
    let err = run(&["--dry-run", "--summaries", "vibes"], FEED).unwrap_err();
    assert_eq!(err.to_string(), "unknown summary: vibes");
}
