"""logpack — bundle rotated log files into a dated archive directory.

Usage:
    logpack SRC_DIR [SRC_DIR ...] [--dest DIR] [--dry-run]
            [--exclude PATTERN ...]

--dry-run reports what would move without touching anything. --exclude
takes a glob and may be given multiple times; when present, the given
patterns REPLACE the built-in DEFAULT_EXCLUDES (already-compressed files),
when absent the defaults apply.
"""
import argparse
import fnmatch

DEFAULT_EXCLUDES = ["*.gz", "*.zip"]


def build_parser():
    parser = argparse.ArgumentParser(
        prog="logpack",
        description="Bundle rotated log files into a dated archive.")
    parser.add_argument("sources", nargs="+", metavar="SRC_DIR",
                        help="directories to sweep for rotated logs")
    parser.add_argument("--dest", default="archive",
                        help="archive directory (default: %(default)s)")
    parser.add_argument("--dry-run", type=bool, default=False,
                        help="only report what would be archived")
    parser.add_argument("--exclude", action="append", default=DEFAULT_EXCLUDES,
                        metavar="PATTERN",
                        help="glob of filenames to skip; replaces the defaults")
    return parser


def parse_cli(argv):
    """Parse argv (excluding the program name) into options."""
    return build_parser().parse_args(argv)


def excluded(name, patterns):
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


def plan(filenames, opts):
    """Decide what to do with each candidate file.

    Returns a sorted list of (verb, filename, dest) actions where verb is
    "move" normally and "would-move" under --dry-run. Excluded files are
    omitted entirely.
    """
    actions = []
    for name in sorted(filenames):
        if excluded(name, opts.exclude):
            continue
        verb = "would-move" if opts.dry_run else "move"
        actions.append((verb, name, opts.dest))
    return actions
