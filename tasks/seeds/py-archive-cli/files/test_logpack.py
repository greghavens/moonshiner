"""Behavior checks for the logpack CLI. Run: python3 test_logpack.py"""
from logpack import DEFAULT_EXCLUDES, parse_cli, plan

FILES = ["app.log", "app.log.1", "app.log.gz", "core.zip", "debug.tmp"]


def main():
    # Defaults: no flags, built-in excludes apply.
    opts = parse_cli(["logs"])
    assert opts.sources == ["logs"]
    assert opts.dest == "archive"
    assert opts.dry_run is False, f"got dry_run={opts.dry_run!r}"
    assert list(opts.exclude) == ["*.gz", "*.zip"], f"got {opts.exclude!r}"
    actions = plan(FILES, opts)
    assert actions == [
        ("move", "app.log", "archive"),
        ("move", "app.log.1", "archive"),
        ("move", "debug.tmp", "archive"),
    ], f"default excludes must skip compressed files, got {actions!r}"

    # User-supplied excludes REPLACE the defaults.
    opts = parse_cli(["logs", "--exclude", "*.tmp"])
    assert list(opts.exclude) == ["*.tmp"], (
        f"--exclude must replace the built-in list, got {opts.exclude!r}")
    actions = plan(FILES, opts)
    assert actions == [
        ("move", "app.log", "archive"),
        ("move", "app.log.1", "archive"),
        ("move", "app.log.gz", "archive"),
        ("move", "core.zip", "archive"),
    ], f"with --exclude '*.tmp' the gz/zip must move again, got {actions!r}"

    # Repeatable.
    opts = parse_cli(["logs", "--exclude", "*.tmp", "--exclude", "*.bak"])
    assert list(opts.exclude) == ["*.tmp", "*.bak"], f"got {opts.exclude!r}"

    # And the module-level default list itself never accumulates junk.
    assert DEFAULT_EXCLUDES == ["*.gz", "*.zip"], (
        f"DEFAULT_EXCLUDES was mutated: {DEFAULT_EXCLUDES!r}")
    opts = parse_cli(["var/log"])
    assert list(opts.exclude) == ["*.gz", "*.zip"], (
        f"a later plain parse still sees pristine defaults, got {opts.exclude!r}")

    # --dry-run is a plain switch: present means on, absent means off.
    opts = parse_cli(["logs", "spool", "--dry-run", "--dest", "cold"])
    assert opts.dry_run is True, f"got dry_run={opts.dry_run!r}"
    assert opts.sources == ["logs", "spool"]
    actions = plan(["app.log"], opts)
    assert actions == [("would-move", "app.log", "cold")], f"got {actions!r}"

    print("all checks passed")


if __name__ == "__main__":
    main()
