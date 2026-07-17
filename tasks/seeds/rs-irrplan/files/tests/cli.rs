use clap::error::ErrorKind;
use clap::Parser;
use rs_irrplan::{render_check, render_plan, Cli, Cmd, Day, ZoneSpec};

fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
    Cli::try_parse_from(args)
}

#[test]
fn parses_plan_subcommand() {
    let cli = parse(&[
        "irrplan", "plan", "--day", "wed", "--budget-min", "120", "--zone", "beds:60", "--zone",
        "orchard:45",
    ])
    .expect("plan should parse");
    match cli.command {
        Cmd::Plan {
            day,
            budget_min,
            zones,
        } => {
            assert_eq!(day, Day::Wed);
            assert_eq!(budget_min, 120);
            assert_eq!(
                zones,
                vec![
                    ZoneSpec {
                        name: "beds".to_string(),
                        minutes: 60
                    },
                    ZoneSpec {
                        name: "orchard".to_string(),
                        minutes: 45
                    },
                ]
            );
        }
        other => panic!("parsed wrong subcommand: {other:?}"),
    }
}

#[test]
fn parses_check_subcommand() {
    let cli = parse(&["irrplan", "check", "--zone", "herbs:15"]).expect("check should parse");
    match cli.command {
        Cmd::Check { zones } => {
            assert_eq!(
                zones,
                vec![ZoneSpec {
                    name: "herbs".to_string(),
                    minutes: 15
                }]
            );
        }
        other => panic!("parsed wrong subcommand: {other:?}"),
    }
}

#[test]
fn rejects_unknown_day_listing_choices() {
    let err = parse(&[
        "irrplan", "plan", "--day", "someday", "--budget-min", "60", "--zone", "beds:30",
    ])
    .unwrap_err();
    assert_eq!(err.kind(), ErrorKind::InvalidValue);
    let msg = err.to_string();
    assert!(msg.contains("possible values"), "no choices listed: {msg}");
    assert!(msg.contains("wed"), "choices should include wed: {msg}");
}

#[test]
fn rejects_budget_outside_range() {
    let err = parse(&[
        "irrplan", "plan", "--day", "mon", "--budget-min", "900", "--zone", "beds:30",
    ])
    .unwrap_err();
    let msg = err.to_string();
    assert!(msg.contains("900 is not in 1..=600"), "got: {msg}");
}

#[test]
fn rejects_malformed_zone_specs_with_pinned_message() {
    for bad in ["beds", "beds:", ":30", "beds:0", "beds:181", "beds:soon"] {
        let err = parse(&[
            "irrplan", "plan", "--day", "mon", "--budget-min", "60", "--zone", bad,
        ])
        .unwrap_err();
        assert_eq!(err.kind(), ErrorKind::ValueValidation, "for {bad}");
        let want = format!("invalid zone spec \"{bad}\": expected NAME:MINUTES with minutes 1..=180");
        assert!(err.to_string().contains(&want), "for {bad}: got {err}");
    }
}

#[test]
fn budget_flag_is_required() {
    let err = parse(&["irrplan", "plan", "--day", "mon", "--zone", "beds:30"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);
    assert!(err.to_string().contains("--budget-min"), "got: {err}");
}

#[test]
fn at_least_one_zone_is_required() {
    let err = parse(&["irrplan", "plan", "--day", "mon", "--budget-min", "60"]).unwrap_err();
    assert_eq!(err.kind(), ErrorKind::MissingRequiredArgument);
    assert!(err.to_string().contains("--zone"), "got: {err}");
}

#[test]
fn bare_invocation_shows_help_as_the_error() {
    let err = parse(&["irrplan"]).unwrap_err();
    assert_eq!(
        err.kind(),
        ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
    );
    let msg = err.to_string();
    assert!(msg.contains("Usage:"), "help text not rendered: {msg}");
    assert!(msg.contains("plan"), "subcommands not listed: {msg}");
}

#[test]
fn unknown_flag_is_rejected() {
    let err = parse(&[
        "irrplan", "plan", "--day", "wed", "--budget-min", "60", "--zones", "beds:30",
    ])
    .unwrap_err();
    assert_eq!(err.kind(), ErrorKind::UnknownArgument);
    assert!(err.to_string().contains("--zones"), "got: {err}");
}

#[test]
fn plan_allocates_in_flag_order_cutting_and_skipping() {
    let zones = vec![
        ZoneSpec { name: "beds".to_string(), minutes: 60 },
        ZoneSpec { name: "orchard".to_string(), minutes: 45 },
        ZoneSpec { name: "lawn".to_string(), minutes: 90 },
        ZoneSpec { name: "herbs".to_string(), minutes: 15 },
    ];
    let want = "watering plan for wed (budget 120 min)\n\
                \x20 beds: 60 min\n\
                \x20 orchard: 45 min\n\
                \x20 lawn: 15 min (cut from 90)\n\
                \x20 herbs: skipped (budget exhausted)\n\
                total: 120 min\n";
    assert_eq!(render_plan(Day::Wed, 120, &zones), want);
}

#[test]
fn plan_with_exact_budget_fit_has_no_cuts() {
    let zones = vec![
        ZoneSpec { name: "beds".to_string(), minutes: 60 },
        ZoneSpec { name: "orchard".to_string(), minutes: 45 },
    ];
    let want = "watering plan for sat (budget 105 min)\n\
                \x20 beds: 60 min\n\
                \x20 orchard: 45 min\n\
                total: 105 min\n";
    assert_eq!(render_plan(Day::Sat, 105, &zones), want);
}

#[test]
fn plan_under_budget_totals_only_what_ran() {
    let zones = vec![ZoneSpec { name: "beds".to_string(), minutes: 30 }];
    let want = "watering plan for mon (budget 600 min)\n\
                \x20 beds: 30 min\n\
                total: 30 min\n";
    assert_eq!(render_plan(Day::Mon, 600, &zones), want);
}

#[test]
fn check_reports_zone_count_and_total() {
    let zones = vec![
        ZoneSpec { name: "beds".to_string(), minutes: 60 },
        ZoneSpec { name: "orchard".to_string(), minutes: 45 },
        ZoneSpec { name: "lawn".to_string(), minutes: 30 },
    ];
    assert_eq!(render_check(&zones), "ok: 3 zones, 135 min total\n");
}
