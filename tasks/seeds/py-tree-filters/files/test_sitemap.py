"""Acceptance checks for sitemap.py. Run: python3 test_sitemap.py"""
import contextlib
import io
import os
import tempfile

from sitemap import main, tree_lines


@contextlib.contextmanager
def site():
    """A throwaway content tree that exercises sorting, nesting and hidden
    entries.  Everything lives inside a TemporaryDirectory."""
    paths = [
        "guides/apple.md",
        "guides/deploy.md",
        "guides/Zebra.md",
        "guides/images/arch.png",
        "posts/drafts/wip.md",
        "posts/hello.md",
        "posts/hello.txt",
        "README.md",
        "robots.txt",
        ".cache/tmp.bin",
        ".hidden.md",
    ]
    with tempfile.TemporaryDirectory() as root:
        for rel in paths:
            p = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write("x\n")
        yield root


DEFAULT_TREE = [
    "guides/",
    "  images/",
    "    arch.png",
    "  apple.md",
    "  deploy.md",
    "  Zebra.md",
    "posts/",
    "  drafts/",
    "    wip.md",
    "  hello.md",
    "  hello.txt",
    "README.md",
    "robots.txt",
]


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

def test_tree_layout_dirs_first_sorted_case_insensitively():
    with site() as root:
        assert tree_lines(root) == DEFAULT_TREE


def test_hidden_entries_are_skipped():
    with site() as root:
        lines = tree_lines(root)
        assert not any(".cache" in ln or ".hidden" in ln or "tmp.bin" in ln
                       for ln in lines)


def test_empty_root_renders_nothing():
    with tempfile.TemporaryDirectory() as root:
        assert tree_lines(root) == []


def test_nested_empty_directory_still_listed():
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "assets"))
        assert tree_lines(root) == ["assets/"]


def test_bad_root_raises_value_error():
    with site() as root:
        for bad in (os.path.join(root, "nope"),
                    os.path.join(root, "README.md")):
            try:
                tree_lines(bad)
                assert False, "accepted non-directory root: %r" % bad
            except ValueError:
                pass


def test_cli_prints_the_tree():
    with site() as root:
        assert run_cli([root]) == DEFAULT_TREE


# ----------------------------- feature: glob filters and a depth cap

def test_include_keeps_matching_files_and_prunes_empty_dirs():
    with site() as root:
        assert tree_lines(root, include=["*.md"]) == [
            "guides/",
            "  apple.md",
            "  deploy.md",
            "  Zebra.md",
            "posts/",
            "  drafts/",
            "    wip.md",
            "  hello.md",
            "README.md",
        ]


def test_include_multiple_patterns_is_a_union():
    with site() as root:
        assert tree_lines(root, include=["*.txt", "*.png"]) == [
            "guides/",
            "  images/",
            "    arch.png",
            "posts/",
            "  hello.txt",
            "robots.txt",
        ]


def test_exclude_directory_is_not_walked():
    with site() as root:
        assert tree_lines(root, exclude=["drafts", "images"]) == [
            "guides/",
            "  apple.md",
            "  deploy.md",
            "  Zebra.md",
            "posts/",
            "  hello.md",
            "  hello.txt",
            "README.md",
            "robots.txt",
        ]


def test_exclude_files_by_glob():
    with site() as root:
        expected = [ln for ln in DEFAULT_TREE
                    if not ln.strip().endswith(".txt")]
        assert tree_lines(root, exclude=["*.txt"]) == expected


def test_exclude_wins_over_include():
    with site() as root:
        assert tree_lines(root, include=["*.md"],
                          exclude=["hello.*", "drafts"]) == [
            "guides/",
            "  apple.md",
            "  deploy.md",
            "  Zebra.md",
            "README.md",
        ]


def test_max_depth_caps_the_walk():
    with site() as root:
        assert tree_lines(root, max_depth=1) == [
            "guides/",
            "posts/",
            "README.md",
            "robots.txt",
        ]
        assert tree_lines(root, max_depth=2) == [
            "guides/",
            "  images/",
            "  apple.md",
            "  deploy.md",
            "  Zebra.md",
            "posts/",
            "  drafts/",
            "  hello.md",
            "  hello.txt",
            "README.md",
            "robots.txt",
        ]


def test_max_depth_must_be_at_least_one():
    with site() as root:
        for bad in (0, -2):
            try:
                tree_lines(root, max_depth=bad)
                assert False, "accepted max_depth=%r" % bad
            except ValueError:
                pass


def test_include_pruning_respects_the_depth_cap():
    with site() as root:
        # arch.png sits at depth 3; with the cap at 2 nothing matches, so
        # the whole chain of directories above it disappears too.
        assert tree_lines(root, include=["*.png"], max_depth=2) == []
        assert tree_lines(root, include=["*.png"], max_depth=3) == [
            "guides/",
            "  images/",
            "    arch.png",
        ]


def test_no_filters_matches_legacy_output():
    with site() as root:
        assert tree_lines(root, include=None, exclude=None,
                          max_depth=None) == DEFAULT_TREE
        # empty pattern lists mean "no filter", not "match nothing"
        assert tree_lines(root, include=[], exclude=[]) == DEFAULT_TREE


def test_cli_filter_flags():
    with site() as root:
        got = run_cli(["--include", "*.md", "--exclude", "drafts",
                       "--max-depth", "2", root])
        assert got == [
            "guides/",
            "  apple.md",
            "  deploy.md",
            "  Zebra.md",
            "posts/",
            "  hello.md",
            "README.md",
        ]


EXISTING = [
    test_tree_layout_dirs_first_sorted_case_insensitively,
    test_hidden_entries_are_skipped,
    test_empty_root_renders_nothing,
    test_nested_empty_directory_still_listed,
    test_bad_root_raises_value_error,
    test_cli_prints_the_tree,
]

FEATURE = [
    test_include_keeps_matching_files_and_prunes_empty_dirs,
    test_include_multiple_patterns_is_a_union,
    test_exclude_directory_is_not_walked,
    test_exclude_files_by_glob,
    test_exclude_wins_over_include,
    test_max_depth_caps_the_walk,
    test_max_depth_must_be_at_least_one,
    test_include_pruning_respects_the_depth_cap,
    test_no_filters_matches_legacy_output,
    test_cli_filter_flags,
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
