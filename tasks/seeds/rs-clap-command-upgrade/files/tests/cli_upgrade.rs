use std::fs;
use std::path::PathBuf;

use rs_clap_command_upgrade::app::{run, Plan};
use rs_clap_command_upgrade::command::command;

fn root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn command_uses_only_the_current_builder_contract() {
    command().validate().expect("the clap 4 command definition must validate");
}

#[test]
fn serve_keeps_defaults_short_options_global_profile_and_counts() {
    assert_eq!(
        run(&["dockctl", "serve"]).plan,
        Some(Plan::Serve {
            profile: "development".to_string(),
            bind: "127.0.0.1".to_string(),
            port: 8080,
            json: false,
            verbosity: 0,
        })
    );
    assert_eq!(
        run(&[
            "dockctl", "--profile", "staging", "serve", "-b", "0.0.0.0", "-p",
            "9012", "-vv",
        ]).plan,
        Some(Plan::Serve {
            profile: "staging".to_string(),
            bind: "0.0.0.0".to_string(),
            port: 9012,
            json: false,
            verbosity: 2,
        })
    );
    assert_eq!(
        run(&["dockctl", "serve", "--json", "--profile=production"]).plan,
        Some(Plan::Serve {
            profile: "production".to_string(),
            bind: "127.0.0.1".to_string(),
            port: 8080,
            json: true,
            verbosity: 0,
        })
    );
}

#[test]
fn completion_keeps_its_required_positional_grammar() {
    assert_eq!(
        run(&["dockctl", "completion", "zsh", "--profile", "ops"]).plan,
        Some(Plan::Completion { profile: "ops".to_string(), shell: "zsh".to_string() })
    );
    let missing = run(&["dockctl", "completion"]);
    assert_eq!(missing.code, 2);
    assert_eq!(missing.stderr, "error: a value is required for <SHELL>\n");
}

#[test]
fn root_and_serve_help_snapshots_are_stable_and_successful() {
    let root_help = run(&["dockctl", "--help"]);
    assert_eq!(root_help.code, 0);
    assert_eq!(root_help.stderr, "");
    assert_eq!(
        root_help.stdout,
        "dockctl 4.5.0\n\
Operate the local document gateway\n\
\n\
Usage: dockctl [OPTIONS] <COMMAND>\n\
\n\
Options:\n\
      --profile <PROFILE>     Runtime profile [default: development]\n\
  -h, --help               Print help\n\
\n\
Commands:\n\
  serve        Start the gateway\n\
  completion   Print shell completion\n"
    );

    let serve_help = run(&["dockctl", "serve", "--help"]);
    assert_eq!(serve_help.code, 0);
    assert_eq!(
        serve_help.stdout,
        "Start the gateway\n\
\n\
Usage: dockctl serve [OPTIONS]\n\
\n\
Options:\n\
  -b, --bind <ADDRESS>        Listen address [default: 127.0.0.1]\n\
  -p, --port <PORT>           Listen port [default: 8080]\n\
      --json                  Emit JSON logs\n\
  -v, --verbose               Increase verbosity\n\
  -h, --help               Print help\n"
    );
}

#[test]
fn usage_errors_remain_status_two_and_do_not_create_a_plan() {
    for (args, message) in [
        (vec!["dockctl"], "error: a subcommand is required\n"),
        (vec!["dockctl", "unknown"], "error: unrecognized subcommand 'unknown'\n"),
        (vec!["dockctl", "serve", "--port", "70000"], "error: invalid value '70000' for '--port'\n"),
        (vec!["dockctl", "serve", "--port", "0"], "error: invalid value '0' for '--port'\n"),
        (
            vec!["dockctl", "serve", "--json", "--verbose"],
            "error: argument '--json' cannot be used with '--verbose'\n",
        ),
    ] {
        let output = run(&args);
        assert_eq!(output.code, 2, "{args:?}");
        assert_eq!(output.stdout, "");
        assert_eq!(output.stderr, message);
        assert_eq!(output.plan, None);
    }
}

#[test]
fn migration_notes_pin_old_and_current_clap_behavior() {
    let notes = fs::read_to_string(root().join("contracts/clap_4_5_migration.md")).unwrap();
    for phrase in [
        "`Arg::takes_value`",
        "`ArgAction::SetTrue`",
        "`get_one`, `get_flag`, `get_count`, and `subcommand`",
        "before or after a subcommand",
        "status 2",
    ] {
        assert!(notes.contains(phrase), "missing protected contract phrase: {phrase}");
    }
}
