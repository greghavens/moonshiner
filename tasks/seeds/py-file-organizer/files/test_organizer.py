"""Acceptance checks for organizer.py. Run: python3 test_organizer.py"""
import contextlib
import datetime
import io
import json
import os
import tempfile

import organizer
from organizer import main, organize

JOURNAL = ".organize-journal.json"

RULES = [
    {"ext": ["jpg", ".PNG"], "dest": "images"},
    {"ext": ["pdf", "txt"], "dest": "docs"},
]


def make_tree(root, files):
    """Create ``files`` (relpath -> content) under root."""
    for rel, content in files.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


def snapshot(root):
    """Set of (relpath, content) for every file under root."""
    state = set()
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            with open(path) as f:
                state.add((os.path.relpath(path, root), f.read()))
    return state


def read(root, rel):
    with open(os.path.join(root, *rel.split("/"))) as f:
        return f.read()


def run_cli(args):
    """Invoke main() capturing stdout; argparse rejections become failures."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = main(args)
    except SystemExit as e:
        raise AssertionError("CLI exited with code %s for args %r" % (e.code, args))
    assert rc == 0, "main() returned %r for args %r" % (rc, args)
    return buf.getvalue().splitlines()


# ---------------------------------------------------------------- existing

def test_moves_claimed_files_case_insensitively():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"photo.jpg": "p", "scan.PDF": "s", "notes.md": "n"})
        moves = organize(root, RULES)
        assert moves == [("photo.jpg", "images/photo.jpg"),
                         ("scan.PDF", "docs/scan.PDF")], moves
        assert read(root, "images/photo.jpg") == "p"
        assert read(root, "docs/scan.PDF") == "s"
        assert read(root, "notes.md") == "n"
        assert not os.path.exists(os.path.join(root, "photo.jpg"))


def test_first_matching_rule_wins():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"pic.jpg": "x"})
        rules = [{"ext": [".jpg"], "dest": "first"},
                 {"ext": ["jpg"], "dest": "second"}]
        assert organize(root, rules) == [("pic.jpg", "first/pic.jpg")]


def test_date_placeholder_expands_from_mtime():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"build.log": "L"})
        when = datetime.datetime(2024, 3, 14, 12, 0).timestamp()
        os.utime(os.path.join(root, "build.log"), (when, when))
        rules = [{"ext": ["log"], "dest": "logs/%Y-%m"}]
        moves = organize(root, rules)
        assert moves == [("build.log", "logs/2024-03/build.log")], moves
        assert read(root, "logs/2024-03/build.log") == "L"


def test_dotfiles_and_unmatched_files_stay_put():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {".env.jpg": "secret", "todo.md": "t"})
        assert organize(root, RULES) == []
        assert read(root, ".env.jpg") == "secret"
        assert read(root, "todo.md") == "t"


def test_subdirectories_are_never_moved():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"vacation.jpg/inner.txt": "i"})
        assert organize(root, RULES) == []
        assert read(root, "vacation.jpg/inner.txt") == "i"


def test_cli_prints_executed_moves():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "b.pdf": "b"})
        out = run_cli([root, "--rule", "jpg:images", "--rule", "pdf:docs"])
        assert out == ["a.jpg -> images/a.jpg", "b.pdf -> docs/b.pdf"], out


# ----------------------------------------------------------------- feature

def test_skip_policy_leaves_the_source_alone():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"dup.txt": "new", "docs/dup.txt": "old"})
        moves = organize(root, RULES, on_collision="skip")
        assert moves == [], moves
        assert read(root, "dup.txt") == "new"
        assert read(root, "docs/dup.txt") == "old"


def test_rename_policy_takes_first_free_suffix():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"report.pdf": "v3",
                         "docs/report.pdf": "v1",
                         "docs/report_1.pdf": "v2"})
        moves = organize(root, RULES, on_collision="rename")
        assert moves == [("report.pdf", "docs/report_2.pdf")], moves
        assert read(root, "docs/report_2.pdf") == "v3"
        assert read(root, "docs/report.pdf") == "v1"


def test_rename_respects_targets_claimed_earlier_in_the_run():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "one", "a_1.jpg": "two",
                         "images/a.jpg": "zero"})
        moves = organize(root, RULES, on_collision="rename")
        assert moves == [("a.jpg", "images/a_1.jpg"),
                         ("a_1.jpg", "images/a_1_1.jpg")], moves
        assert read(root, "images/a_1.jpg") == "one"
        assert read(root, "images/a_1_1.jpg") == "two"
        assert read(root, "images/a.jpg") == "zero"


def test_overwrite_policy_replaces_the_destination():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"dup.txt": "new", "docs/dup.txt": "old"})
        moves = organize(root, RULES, on_collision="overwrite")
        assert moves == [("dup.txt", "docs/dup.txt")], moves
        assert read(root, "docs/dup.txt") == "new"
        assert not os.path.exists(os.path.join(root, "dup.txt"))


def test_plan_reports_actions_without_touching_disk():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "dup.txt": "new", "docs/dup.txt": "old"})
        before = snapshot(root)
        actions = organizer.plan(root, RULES, on_collision="skip")
        assert actions == [("move", "a.jpg", "images/a.jpg"),
                           ("skip", "dup.txt", "docs/dup.txt")], actions
        assert snapshot(root) == before


def test_dry_run_returns_the_plan_and_changes_nothing():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "dup.txt": "new", "docs/dup.txt": "old"})
        before = snapshot(root)
        actions = organize(root, RULES, on_collision="overwrite", dry_run=True)
        assert actions == [("move", "a.jpg", "images/a.jpg"),
                           ("overwrite", "dup.txt", "docs/dup.txt")], actions
        assert snapshot(root) == before
        assert not os.path.exists(os.path.join(root, JOURNAL))


def test_journal_records_executed_moves_and_undo_reverses_them():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "dup.txt": "new", "docs/dup.txt": "old"})
        organize(root, RULES, on_collision="skip")
        with open(os.path.join(root, JOURNAL)) as f:
            entries = json.load(f)
        assert entries == [{"src": "a.jpg", "dst": "images/a.jpg"}], entries
        undone = organizer.undo_last(root)
        assert undone == [("images/a.jpg", "a.jpg")], undone
        assert read(root, "a.jpg") == "a"
        assert not os.path.exists(os.path.join(root, "images", "a.jpg"))
        assert not os.path.exists(os.path.join(root, JOURNAL))


def test_journal_only_remembers_the_most_recent_run():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a"})
        organize(root, RULES)
        make_tree(root, {"b.pdf": "b"})
        organize(root, RULES)
        assert organizer.undo_last(root) == [("docs/b.pdf", "b.pdf")]
        assert read(root, "images/a.jpg") == "a"
        assert read(root, "b.pdf") == "b"


def test_undo_skips_entries_whose_destination_vanished():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "b.pdf": "b"})
        organize(root, RULES)
        os.remove(os.path.join(root, "images", "a.jpg"))
        undone = organizer.undo_last(root)
        assert undone == [("docs/b.pdf", "b.pdf")], undone
        assert read(root, "b.pdf") == "b"
        assert not os.path.exists(os.path.join(root, "a.jpg"))


def test_undo_with_no_journal_raises_value_error():
    with tempfile.TemporaryDirectory() as root:
        try:
            organizer.undo_last(root)
            assert False, "undo_last() accepted a directory with no journal"
        except ValueError:
            pass


def test_cli_dry_run_prints_the_plan():
    with tempfile.TemporaryDirectory() as root:
        make_tree(root, {"a.jpg": "a", "b.jpg": "b", "images/b.jpg": "old"})
        out = run_cli([root, "--rule", "jpg:images",
                       "--on-collision", "skip", "--dry-run"])
        assert out == ["move a.jpg -> images/a.jpg",
                       "skip b.jpg -> images/b.jpg"], out
        assert read(root, "b.jpg") == "b"
        assert read(root, "images/b.jpg") == "old"
        assert not os.path.exists(os.path.join(root, JOURNAL))


EXISTING = [
    test_moves_claimed_files_case_insensitively,
    test_first_matching_rule_wins,
    test_date_placeholder_expands_from_mtime,
    test_dotfiles_and_unmatched_files_stay_put,
    test_subdirectories_are_never_moved,
    test_cli_prints_executed_moves,
]

FEATURE = [
    test_skip_policy_leaves_the_source_alone,
    test_rename_policy_takes_first_free_suffix,
    test_rename_respects_targets_claimed_earlier_in_the_run,
    test_overwrite_policy_replaces_the_destination,
    test_plan_reports_actions_without_touching_disk,
    test_dry_run_returns_the_plan_and_changes_nothing,
    test_journal_records_executed_moves_and_undo_reverses_them,
    test_journal_only_remembers_the_most_recent_run,
    test_undo_skips_entries_whose_destination_vanished,
    test_undo_with_no_journal_raises_value_error,
    test_cli_dry_run_prints_the_plan,
]


def main_check():
    failures = 0
    for t in EXISTING + FEATURE:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main_check()
