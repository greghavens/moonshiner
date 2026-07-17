//! Acceptance tests for the subcommand / passthrough / usage-text feature.
//! These are the contract for the new behavior; the suite in parser.rs must
//! keep passing unchanged alongside them.

use rs_cliargs::{CliError, Command};

fn relay() -> Command {
    Command::new("relay")
        .switch("verbose", "Chatty output")
        .option("config", "FILE", "Config file path")
        .subcommand(
            Command::new("fetch")
                .about("Download remote state")
                .switch("force", "Refetch even if fresh")
                .option("depth", "N", "History depth")
                .positional("remote", "Remote name"),
        )
        .subcommand(
            Command::new("push")
                .about("Upload local state")
                .switch("verbose", "Per-file progress")
                .positional("remote", "Remote name"),
        )
}

#[test]
fn dispatches_to_subcommand() {
    let m = relay()
        .parse(&["--verbose", "fetch", "--force", "origin"])
        .unwrap();
    assert!(m.switch("verbose"));
    let (name, sub) = m.subcommand().expect("subcommand should be present");
    assert_eq!(name, "fetch");
    assert!(sub.switch("force"));
    assert_eq!(sub.positional("remote"), Some("origin"));
}

#[test]
fn no_subcommand_given_is_ok_and_none() {
    let m = relay().parse(&["--verbose"]).unwrap();
    assert!(m.switch("verbose"));
    assert!(m.subcommand().is_none());
}

#[test]
fn nested_subcommands_dispatch_two_levels() {
    let cmd = Command::new("infra").subcommand(
        Command::new("dns").about("Manage DNS records").subcommand(
            Command::new("add")
                .option("ttl", "SECS", "Record TTL")
                .positional("zone", "Zone name"),
        ),
    );
    let m = cmd
        .parse(&["dns", "add", "example.org", "--ttl", "300"])
        .unwrap();
    let (name, dns) = m.subcommand().unwrap();
    assert_eq!(name, "dns");
    let (name, add) = dns.subcommand().unwrap();
    assert_eq!(name, "add");
    assert_eq!(add.positional("zone"), Some("example.org"));
    assert_eq!(add.int("ttl").unwrap(), Some(300));
}

#[test]
fn unknown_subcommand_is_an_error() {
    let err = relay().parse(&["frob"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownSubcommand {
            name: "frob".to_string()
        }
    );
    assert_eq!(err.to_string(), "unknown subcommand 'frob'");
}

#[test]
fn parent_flags_are_unknown_inside_subcommand() {
    // --config is declared on the root, not on fetch; after dispatch every
    // token belongs to the subcommand.
    let err = relay().parse(&["fetch", "origin", "--config=x"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownFlag {
            name: "--config".to_string()
        }
    );
}

#[test]
fn subcommand_flags_before_the_name_are_root_errors() {
    let err = relay().parse(&["--force", "fetch", "origin"]).unwrap_err();
    assert_eq!(
        err,
        CliError::UnknownFlag {
            name: "--force".to_string()
        }
    );
}

#[test]
fn same_flag_name_binds_to_the_command_where_it_appears() {
    let m = relay().parse(&["--verbose", "push", "origin"]).unwrap();
    assert!(m.switch("verbose"));
    let (_, push) = m.subcommand().unwrap();
    assert!(!push.switch("verbose"));

    let m = relay().parse(&["push", "origin", "--verbose"]).unwrap();
    assert!(!m.switch("verbose"));
    let (_, push) = m.subcommand().unwrap();
    assert!(push.switch("verbose"));
}

#[test]
fn option_value_is_never_a_subcommand() {
    let m = relay().parse(&["--config", "fetch"]).unwrap();
    assert_eq!(m.value("config"), Some("fetch"));
    assert!(m.subcommand().is_none());
}

#[test]
fn passthrough_collects_everything_after_separator() {
    let cmd = Command::new("run")
        .switch("quiet", "Say less")
        .positional("script", "Script path");
    let m = cmd
        .parse(&["--quiet", "build.sh", "--", "--verbose", "-x", "old.log"])
        .unwrap();
    assert!(m.switch("quiet"));
    assert_eq!(m.positional("script"), Some("build.sh"));
    assert_eq!(m.trailing(), ["--verbose", "-x", "old.log"]);
}

#[test]
fn passthrough_may_be_the_entire_argument_vector() {
    let cmd = Command::new("exec");
    let m = cmd.parse(&["--", "rm", "-rf", "scratch"]).unwrap();
    assert_eq!(m.trailing(), ["rm", "-rf", "scratch"]);
    assert!(m.subcommand().is_none());
}

#[test]
fn trailing_is_empty_without_separator() {
    let m = relay().parse(&["fetch", "origin"]).unwrap();
    assert!(m.trailing().is_empty());
    let (_, fetch) = m.subcommand().unwrap();
    assert!(fetch.trailing().is_empty());
}

#[test]
fn positionals_are_not_filled_from_trailing_tokens() {
    let cmd = Command::new("run").positional("script", "Script path");
    let err = cmd.parse(&["--", "build.sh"]).unwrap_err();
    assert_eq!(
        err,
        CliError::MissingPositional {
            name: "script".to_string()
        }
    );
}

#[test]
fn later_separators_are_kept_verbatim() {
    let cmd = Command::new("exec");
    let m = cmd.parse(&["--", "a", "--", "b"]).unwrap();
    assert_eq!(m.trailing(), ["a", "--", "b"]);
}

#[test]
fn passthrough_inside_a_subcommand() {
    let m = relay()
        .parse(&["fetch", "origin", "--", "--force"])
        .unwrap();
    assert!(m.trailing().is_empty());
    let (_, fetch) = m.subcommand().unwrap();
    assert_eq!(fetch.trailing(), ["--force"]);
    assert!(!fetch.switch("force"));
}

#[test]
fn usage_for_root_with_subcommands() {
    let cmd = Command::new("relay")
        .about("Relay state between environments")
        .option("config", "FILE", "Config file path")
        .switch("verbose", "Chatty output")
        .subcommand(Command::new("fetch").about("Download remote state"))
        .subcommand(Command::new("push").about("Upload local state"));
    let expected = "\
Relay state between environments

Usage: relay [OPTIONS]
       relay <COMMAND>

Commands:
  fetch  Download remote state
  push   Upload local state

Options:
  --config <FILE>  Config file path
  --verbose        Chatty output
";
    assert_eq!(cmd.usage(), expected);
}

#[test]
fn usage_for_leaf_with_arguments_and_options() {
    let cmd = Command::new("copy")
        .option("jobs", "N", "Parallel workers")
        .switch("force", "Overwrite existing files")
        .positional("src", "Source path")
        .positional("dest", "Destination path");
    let expected = "\
Usage: copy [OPTIONS] <src> <dest>

Arguments:
  <src>   Source path
  <dest>  Destination path

Options:
  --jobs <N>  Parallel workers
  --force     Overwrite existing files
";
    assert_eq!(cmd.usage(), expected);
}

#[test]
fn usage_minimal_command() {
    assert_eq!(Command::new("ping").usage(), "Usage: ping\n");
}

#[test]
fn usage_lists_sections_in_declaration_order() {
    let cmd = Command::new("top")
        .subcommand(Command::new("zeta").about("Comes first anyway"))
        .subcommand(Command::new("alpha").about("Comes second"));
    let expected = "\
Usage: top
       top <COMMAND>

Commands:
  zeta   Comes first anyway
  alpha  Comes second
";
    assert_eq!(cmd.usage(), expected);
}
