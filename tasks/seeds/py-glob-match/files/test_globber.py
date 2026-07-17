"""Acceptance tests for the ignore-pattern glob matcher. Run: python3 test_globber.py"""
import time


def main():
    from globber import glob_match, glob_filter

    # -- literal patterns --
    assert glob_match("main.py", "main.py")
    assert not glob_match("main.py", "main.pyc")
    assert not glob_match("main.py", "Main.py"), "matching is case-sensitive"
    assert glob_match("", "")
    assert not glob_match("", "a")

    # -- star: any run of characters, including none, but never a slash --
    assert glob_match("*.py", "main.py")
    assert not glob_match("*.py", "main.pyc")
    assert glob_match("*", "")
    assert glob_match("*", "anything")
    assert not glob_match("*", "a/b")
    assert glob_match("a*", "a")
    assert glob_match("*c", "abc")
    assert glob_match("a*c*e", "abcde")
    assert not glob_match("a*c*e", "abcdef")
    assert glob_match("a**b", "ab"), "adjacent stars collapse"
    assert glob_match("a**b", "axyzb")
    assert not glob_match("src/*", "src/a/b"), "star must not cross a slash"
    assert glob_match("src/*", "src/a")

    # -- question mark: exactly one character, never a slash --
    assert glob_match("?", "x")
    assert not glob_match("?", "")
    assert not glob_match("?", "xy")
    assert not glob_match("a?b", "a/b")
    assert glob_match("test_?.py", "test_a.py")
    assert not glob_match("test_?.py", "test_ab.py")

    # -- character classes --
    assert glob_match("file.[ch]", "file.c")
    assert glob_match("file.[ch]", "file.h")
    assert not glob_match("file.[ch]", "file.o")
    assert glob_match("[a-z]", "q")
    assert not glob_match("[a-z]", "Q")
    assert glob_match("[0-9a-f]", "b")
    assert glob_match("[0-9a-f]", "7")
    assert not glob_match("[0-9a-f]", "g")
    assert not glob_match("[abc]", ""), "a class consumes exactly one char"
    assert not glob_match("[abc]", "ab")

    # -- negated classes --
    assert glob_match("[!abc]", "d")
    assert not glob_match("[!abc]", "a")
    assert glob_match("log[!0-9]", "logs")
    assert not glob_match("log[!0-9]", "log1")
    # '!' anywhere but first is an ordinary member
    assert glob_match("[a!]", "!")
    assert glob_match("[a!]", "a")

    # -- ']' as the first member is literal; '-' at the edges is literal --
    assert glob_match("[]]", "]")
    assert not glob_match("[]]", "x")
    assert glob_match("[!]]", "x")
    assert not glob_match("[!]]", "]")
    assert glob_match("[-a]", "-")
    assert glob_match("[-a]", "a")
    assert glob_match("[a-]", "-")
    assert not glob_match("[a-]", "b")

    # -- a reversed range matches nothing --
    assert not glob_match("[z-a]", "m")
    assert not glob_match("[z-a]", "z")

    # -- an unclosed '[' is a literal bracket --
    assert glob_match("a[b", "a[b")
    assert not glob_match("a[b", "ab")
    assert glob_match("[", "[")
    assert glob_match("x[]", "x[]"), "'[]' never closes, so it is two literals"

    # -- classes match exactly what they list, slash included if listed --
    assert glob_match("a[/]b", "a/b")
    assert not glob_match("a[!/]b", "a/b")

    # -- realistic path patterns --
    assert glob_match("src/*/test_?.py", "src/app/test_a.py")
    assert not glob_match("src/*/test_?.py", "src/app/sub/test_a.py")
    assert not glob_match("src/*/test_?.py", "src/test_a.py")
    assert glob_match("build/*.o", "build/main.o")
    assert glob_match("*.tar.[gx]z", "backup.tar.gz")
    assert glob_match("*.tar.[gx]z", "backup.tar.xz")
    assert not glob_match("*.tar.[gx]z", "backup.tar.bz")

    # -- glob_filter keeps input order --
    names = ["a.py", "b.txt", "sub/c.py", "d.py"]
    assert glob_filter("*.py", names) == ["a.py", "d.py"]
    assert glob_filter("*", ["x", "y/z"]) == ["x"]
    assert glob_filter("nope", names) == []

    # -- pathological patterns must not blow up exponentially --
    started = time.monotonic()
    hay = "a" * 60
    assert not glob_match("a*a*a*a*a*a*a*a*a*b", hay)
    assert glob_match("a*a*a*a*a*a*a*a*a*b", hay + "b")
    assert glob_match("*a*a*a*a*a*a*a*a*a*", hay)
    elapsed = time.monotonic() - started
    assert elapsed < 10.0, f"matcher too slow: {elapsed:.1f}s (exponential backtracking?)"

    print("all globber checks passed")


if __name__ == "__main__":
    main()
