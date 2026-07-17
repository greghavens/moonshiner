//! Regression suite for the shipped parser behavior. Every test in this file
//! passes today and must keep passing.

use rs_cliargs::{CliError, Command};

fn deploy_cmd() -> Command {
    Command::new("deploy")
        .switch("verbose", "Chatty output")
        .switch("dry-run", "Plan only, change nothing")
        .option("jobs", "N", "Parallel workers")
        .option("tag", "NAME", "Release tag to pin")
}

#[test]
fn switch_present_and_absent() {
    let m = deploy_cmd().parse(&["--verbose"]).unwrap();
    assert!(m.switch("verbose"));
    assert!(!m.switch("dry-run"));
}

#[test]
fn switch_repeated_is_idempotent() {
    let m = deploy_cmd().parse(&["--verbose", "--verbose"]).unwrap();
    assert!(m.switch("verbose"));
}

#[test]
fn option_space_form() {
    let m = deploy_cmd().parse(&["--jobs", "8"]).unwrap();
    assert_eq!(m.value("jobs"), Some("8"));
}

#[test]
fn option_equals_form() {
    let m = deploy_cmd().parse(&["--jobs=8"]).unwrap();
    assert_eq!(m.value("jobs"), Some("8"));
}

#[test]
fn option_equals_with_empty_value() {
    let m = deploy_cmd().parse(&["--tag="]).unwrap();
    assert_eq!(m.value("tag"), Some(""));
}

#[test]
fn option_value_may_contain_equals() {
    let m = deploy_cmd().parse(&["--tag=a=b"]).unwrap();
    assert_eq!(m.value("tag"), Some("a=b"));
}

#[test]
fn repeated_option_last_wins() {
    let m = deploy_cmd().parse(&["--jobs", "2", "--jobs=9"]).unwrap();
    assert_eq!(m.value("jobs"), Some("9"));
}

#[test]
fn unset_option_is_none() {
    let m = deploy_cmd().parse(&[]).unwrap();
    assert_eq!(m.value("tag"), None);
    assert!(!m.switch("verbose"));
}

#[test]
fn unknown_flag_is_an_error() {
    let err = deploy_cmd().parse(&["--bogus"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownFlag {
            name: "--bogus".to_string()
        }
    );
}

#[test]
fn unknown_flag_with_inline_value_reports_flag_part() {
    let err = deploy_cmd().parse(&["--bogus=3"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownFlag {
            name: "--bogus".to_string()
        }
    );
}

#[test]
fn single_dash_token_is_rejected() {
    let err = deploy_cmd().parse(&["-v"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownFlag {
            name: "-v".to_string()
        }
    );
}

#[test]
fn option_missing_value_at_end() {
    let err = deploy_cmd().parse(&["--verbose", "--jobs"]).unwrap_err();
    assert_eq!(
        err,
        CliError::MissingValue {
            name: "--jobs".to_string()
        }
    );
}

#[test]
fn option_consumes_next_token_verbatim() {
    // The token after a value option is its value, even if it looks flag-ish.
    let m = deploy_cmd().parse(&["--tag", "--verbose"]).unwrap();
    assert_eq!(m.value("tag"), Some("--verbose"));
    assert!(!m.switch("verbose"));
}

#[test]
fn switch_rejects_inline_value() {
    let err = deploy_cmd().parse(&["--verbose=yes"]).unwrap_err();
    assert_eq!(
        err,
        CliError::InvalidValue {
            name: "--verbose".to_string(),
            value: "yes".to_string(),
            expected: "no value",
        }
    );
}

#[test]
fn positionals_fill_in_declaration_order() {
    let cmd = Command::new("copy")
        .positional("src", "Source path")
        .positional("dest", "Destination path");
    let m = cmd.parse(&["a.txt", "b.txt"]).unwrap();
    assert_eq!(m.positional("src"), Some("a.txt"));
    assert_eq!(m.positional("dest"), Some("b.txt"));
}

#[test]
fn flags_and_positionals_interleave() {
    let cmd = Command::new("copy")
        .switch("force", "Overwrite")
        .option("jobs", "N", "Workers")
        .positional("src", "Source")
        .positional("dest", "Destination");
    let m = cmd
        .parse(&["--jobs", "4", "a.txt", "--force", "b.txt"])
        .unwrap();
    assert_eq!(m.positional("src"), Some("a.txt"));
    assert_eq!(m.positional("dest"), Some("b.txt"));
    assert!(m.switch("force"));
    assert_eq!(m.int("jobs").unwrap(), Some(4));
}

#[test]
fn extra_positional_is_an_error() {
    let cmd = Command::new("copy").positional("src", "Source");
    let err = cmd.parse(&["a.txt", "b.txt"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnexpectedPositional {
            value: "b.txt".to_string()
        }
    );
}

#[test]
fn missing_positional_is_an_error() {
    let cmd = Command::new("copy")
        .positional("src", "Source")
        .positional("dest", "Destination");
    let err = cmd.parse(&["a.txt"]).unwrap_err();
    assert_eq!(
        err,
        CliError::MissingPositional {
            name: "dest".to_string()
        }
    );
}

#[test]
fn bare_dash_is_a_positional() {
    // Convention: "-" means stdin, so it must flow through as a value.
    let cmd = Command::new("lint").positional("file", "Input file");
    let m = cmd.parse(&["-"]).unwrap();
    assert_eq!(m.positional("file"), Some("-"));
}

#[test]
fn int_getter_absent_is_ok_none() {
    let m = deploy_cmd().parse(&[]).unwrap();
    assert_eq!(m.int("jobs").unwrap(), None);
}

#[test]
fn int_getter_rejects_garbage() {
    let m = deploy_cmd().parse(&["--jobs", "many"]).unwrap();
    let err = m.int("jobs").unwrap_err();
    assert_eq!(
        err,
        CliError::InvalidValue {
            name: "--jobs".to_string(),
            value: "many".to_string(),
            expected: "an integer",
        }
    );
}

#[test]
fn int_getter_parses_negative() {
    let m = deploy_cmd().parse(&["--jobs=-3"]).unwrap();
    assert_eq!(m.int("jobs").unwrap(), Some(-3));
}

#[test]
fn error_display_messages() {
    assert_eq!(
        CliError::UnknownFlag {
            name: "--bogus".to_string()
        }
        .to_string(),
        "unknown flag '--bogus'"
    );
    assert_eq!(
        CliError::MissingValue {
            name: "--jobs".to_string()
        }
        .to_string(),
        "flag '--jobs' expects a value"
    );
    assert_eq!(
        CliError::InvalidValue {
            name: "--jobs".to_string(),
            value: "many".to_string(),
            expected: "an integer",
        }
        .to_string(),
        "invalid value 'many' for '--jobs': expected an integer"
    );
    assert_eq!(
        CliError::UnexpectedPositional {
            value: "b.txt".to_string()
        }
        .to_string(),
        "unexpected argument 'b.txt'"
    );
    assert_eq!(
        CliError::MissingPositional {
            name: "dest".to_string()
        }
        .to_string(),
        "missing required argument <dest>"
    );
}
