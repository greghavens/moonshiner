"""Acceptance tests for argkit, the in-house argument parser.

Run: python3 test_argkit.py
"""
import sys

from argkit import HelpRequested, Parser, UsageError


def report_parser():
    p = Parser(prog="reportgen", description="Generate usage reports.")
    p.add_argument("source", help="input events file")
    p.add_argument("dest", help="where the report lands")
    p.add_argument("--format", "-f", default="text", help="output format")
    p.add_argument("--limit", type=int, default=20, help="max rows")
    p.add_argument("--verbose", "-v", flag=True, help="chatty progress output")
    p.add_argument("--tag", required=True, help="report tag")
    return p


def jobctl_parser():
    p = Parser(prog="jobctl", description="Control the batch job runner.")
    p.add_argument("--config", default="jobs.ini", help="config file path")
    add = p.add_command("add", description="Queue a new job.")
    add.add_argument("name", help="job name")
    add.add_argument("--priority", type=int, default=5, help="1 is most urgent")
    ls = p.add_command("list", description="Show queued jobs.")
    ls.add_argument("--all", "-a", flag=True, help="include finished jobs")
    return p, add, ls


REPORTGEN_USAGE = ("usage: reportgen [--help] [--format FORMAT] [--limit LIMIT] "
                   "[--verbose] --tag TAG source dest")

REPORTGEN_HELP = """\
usage: reportgen [--help] [--format FORMAT] [--limit LIMIT] [--verbose] --tag TAG source dest

Generate usage reports.

arguments:
  source  input events file
  dest    where the report lands

options:
  --help, -h           show this help message and exit
  --format, -f FORMAT  output format (default: text)
  --limit LIMIT        max rows (default: 20)
  --verbose, -v        chatty progress output
  --tag TAG            report tag
"""

JOBCTL_HELP = """\
usage: jobctl [--help] [--config CONFIG] <command> ...

Control the batch job runner.

commands:
  add   Queue a new job.
  list  Show queued jobs.

options:
  --help, -h       show this help message and exit
  --config CONFIG  config file path (default: jobs.ini)
"""

JOBCTL_ADD_HELP = """\
usage: jobctl add [--help] [--priority PRIORITY] name

Queue a new job.

arguments:
  name  job name

options:
  --help, -h           show this help message and exit
  --priority PRIORITY  1 is most urgent (default: 5)
"""


def expect_usage_error(parser, argv, code, message, usage=None):
    try:
        parser.parse(argv)
    except UsageError as e:
        assert e.code == code, (e.code, code, argv)
        assert e.message == message, (e.message, message)
        expected_usage = usage if usage is not None else parser.format_usage()
        assert e.usage == expected_usage, (e.usage, expected_usage)
        assert str(e) == f"{expected_usage}\nerror: {message}", str(e)
        return e
    raise AssertionError(f"parse({argv!r}) should raise UsageError({code})")


def test_no_stdlib_parser_backdoor():
    # argkit exists because argparse is unavailable on the target image.
    for mod in ("argparse", "optparse", "getopt"):
        assert mod not in sys.modules, f"argkit must not be built on {mod}"


def test_usage_line_pinned():
    assert report_parser().format_usage() == REPORTGEN_USAGE


def test_help_text_pinned():
    assert report_parser().format_help() == REPORTGEN_HELP


def test_positionals_and_defaults():
    ns = report_parser().parse(["events.log", "out.txt", "--tag", "weekly"])
    assert ns.source == "events.log"
    assert ns.dest == "out.txt"
    assert ns.format == "text"
    assert ns.limit == 20
    assert ns.verbose is False
    assert ns.tag == "weekly"


def test_flags_interleaved_and_equals_and_aliases():
    ns = report_parser().parse(
        ["--verbose", "events.log", "--format=json", "out.txt", "--tag", "t1"])
    assert ns.verbose is True and ns.format == "json"
    assert ns.source == "events.log" and ns.dest == "out.txt"

    ns = report_parser().parse(["-v", "-f", "csv", "a", "b", "--tag", "t2"])
    assert ns.verbose is True and ns.format == "csv"

    # a repeated option: the last occurrence wins
    ns = report_parser().parse(["a", "b", "--tag", "x", "--limit", "5", "--limit", "9"])
    assert ns.limit == 9 and ns.tag == "x"


def test_type_coercion_and_bad_values():
    ns = report_parser().parse(["a", "b", "--tag", "t", "--limit", "250"])
    assert ns.limit == 250 and isinstance(ns.limit, int)
    expect_usage_error(report_parser(), ["a", "b", "--tag", "t", "--limit", "abc"],
                       "bad-value", "invalid int for --limit: 'abc'")

    p = Parser(prog="wait")
    p.add_argument("seconds", type=float)
    assert p.parse(["1.5"]).seconds == 1.5
    expect_usage_error(p, ["soon"], "bad-value", "invalid float for seconds: 'soon'")


def test_unknown_and_malformed_options():
    expect_usage_error(report_parser(), ["a", "b", "--tag", "t", "--frobnicate"],
                       "unknown-option", "unrecognized option: --frobnicate")
    expect_usage_error(report_parser(), ["a", "b", "--tag", "t", "-z"],
                       "unknown-option", "unrecognized option: -z")
    expect_usage_error(report_parser(), ["a", "b", "--tag"],
                       "missing-value", "option --tag expects a value")
    expect_usage_error(report_parser(), ["a", "b", "--tag", "t", "--verbose=1"],
                       "unexpected-value", "option --verbose does not take a value")


def test_missing_and_extra_arguments():
    expect_usage_error(report_parser(), ["onlyone", "--tag", "t"],
                       "missing-argument", "missing required argument: dest")
    expect_usage_error(report_parser(), ["--tag", "t"],
                       "missing-argument", "missing required argument: source")
    expect_usage_error(report_parser(), ["a", "b", "zap", "--tag", "t"],
                       "extra-argument", "unexpected extra argument: 'zap'")
    expect_usage_error(report_parser(), ["a", "b"],
                       "missing-option", "missing required option: --tag")


def test_double_dash_stops_option_parsing():
    ns = report_parser().parse(["--tag", "t", "--", "--verbose", "-f"])
    assert ns.source == "--verbose"
    assert ns.dest == "-f"
    assert ns.verbose is False and ns.format == "text"


def test_help_raises_in_process():
    try:
        report_parser().parse(["-h"])
    except HelpRequested as e:
        assert e.text == REPORTGEN_HELP
    else:
        raise AssertionError("-h should raise HelpRequested")

    # tokens are handled left to right: help before a later junk option...
    try:
        report_parser().parse(["--help", "--frobnicate"])
    except HelpRequested as e:
        assert e.text == REPORTGEN_HELP
    else:
        raise AssertionError("--help should raise HelpRequested")
    # ...and an earlier junk option errors before a later --help
    expect_usage_error(report_parser(), ["--frobnicate", "--help"],
                       "unknown-option", "unrecognized option: --frobnicate")


def test_subcommand_help_pinned():
    p, add, _ = jobctl_parser()
    assert p.format_help() == JOBCTL_HELP
    assert add.format_help() == JOBCTL_ADD_HELP
    try:
        p.parse(["add", "-h"])
    except HelpRequested as e:
        assert e.text == JOBCTL_ADD_HELP
    else:
        raise AssertionError("'add -h' should raise HelpRequested with the sub help")


def test_subcommand_parsing():
    p, _, _ = jobctl_parser()
    ns = p.parse(["--config", "prod.ini", "add", "nightly-etl", "--priority", "2"])
    assert ns.command == "add"
    assert ns.config == "prod.ini"
    assert ns.name == "nightly-etl"
    assert ns.priority == 2

    p2, _, _ = jobctl_parser()
    ns = p2.parse(["list"])
    assert ns.command == "list"
    assert ns.config == "jobs.ini"
    assert ns.all is False

    p3, _, _ = jobctl_parser()
    ns = p3.parse(["list", "-a"])
    assert ns.all is True


def test_subcommand_errors():
    p, _, _ = jobctl_parser()
    expect_usage_error(p, ["frob"], "unknown-command", "unknown command: 'frob'")
    p, _, _ = jobctl_parser()
    expect_usage_error(p, [], "missing-command", "missing command")
    # errors inside a command carry the command's usage line
    p, add, _ = jobctl_parser()
    expect_usage_error(p, ["add"], "missing-argument",
                       "missing required argument: name",
                       usage="usage: jobctl add [--help] [--priority PRIORITY] name")
    p, add, _ = jobctl_parser()
    expect_usage_error(p, ["add", "n1", "--priority", "high"], "bad-value",
                       "invalid int for --priority: 'high'",
                       usage="usage: jobctl add [--help] [--priority PRIORITY] name")


def test_dest_and_metavar_derivation():
    p = Parser(prog="pruner")
    p.add_argument("--max-rows", type=int, default=10, help="row cap")
    assert p.format_usage() == "usage: pruner [--help] [--max-rows MAX_ROWS]"
    ns = p.parse(["--max-rows", "3"])
    assert ns.max_rows == 3
    assert p.parse([]).max_rows == 10


def test_definition_time_validation():
    p = Parser(prog="x")
    p.add_argument("--flagged", flag=True)
    for bad in (
        lambda: p.add_argument("--flagged"),                       # duplicate name
        lambda: p.add_argument("--other", "-f", flag=True) or p.add_argument("--more", "-f"),
        lambda: p.add_argument("--bad", flag=True, default=True),  # flag with default
        lambda: p.add_argument("--bad2", flag=True, type=int),     # flag with type
        lambda: p.add_argument("--bad3", required=True, default="x"),
        lambda: p.add_argument("-s"),                              # no long form
        lambda: Parser(prog="y").add_argument("a", "b"),           # two positional names
    ):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError at definition time")

    q = Parser(prog="q")
    q.add_argument("pos")
    try:
        q.add_command("go")
    except ValueError:
        pass
    else:
        raise AssertionError("mixing positionals and commands must fail")

    r = Parser(prog="r")
    r.add_command("go")
    try:
        r.add_argument("pos")
    except ValueError:
        pass
    else:
        raise AssertionError("mixing commands and positionals must fail")


def main():
    tests = [
        test_no_stdlib_parser_backdoor,
        test_usage_line_pinned,
        test_help_text_pinned,
        test_positionals_and_defaults,
        test_flags_interleaved_and_equals_and_aliases,
        test_type_coercion_and_bad_values,
        test_unknown_and_malformed_options,
        test_missing_and_extra_arguments,
        test_double_dash_stops_option_parsing,
        test_help_raises_in_process,
        test_subcommand_help_pinned,
        test_subcommand_parsing,
        test_subcommand_errors,
        test_dest_and_metavar_derivation,
        test_definition_time_validation,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
