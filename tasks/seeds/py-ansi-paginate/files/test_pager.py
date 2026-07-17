"""Acceptance checks for pager.py. Run: python3 test_pager.py"""
from pager import page_count, paginate, render_page, wrap_line

RED = "\x1b[31m"
BOLD = "\x1b[1m"
RESET = "\x1b[0m"
FG256 = "\x1b[38;5;196m"


# ---------------------------------------------------------------- existing

def test_wrap_plain_text():
    assert wrap_line("abcdef", 3) == ["abc", "def"]
    assert wrap_line("abcdefg", 3) == ["abc", "def", "g"]
    assert wrap_line("abc", 3) == ["abc"]      # exact fit, no split
    assert wrap_line("ab", 3) == ["ab"]
    assert wrap_line("", 5) == [""]            # blank lines survive


def test_dimension_validation():
    try:
        wrap_line("x", 0)
        assert False, "width 0 accepted"
    except ValueError:
        pass
    try:
        paginate("x", 10, 0)
        assert False, "height 0 accepted"
    except ValueError:
        pass


def test_paginate_plain_text():
    text = "alpha beta\ngamma\n\ndelta epsilon zeta"
    pages = paginate(text, 8, 3)
    assert pages == [
        ["alpha be", "ta", "gamma"],
        ["", "delta ep", "silon ze"],
        ["ta"],
    ]
    assert page_count(text, 8, 3) == 3
    assert paginate("", 10, 5) == [[""]]


def test_render_page_footer():
    text = "alpha beta\ngamma\n\ndelta epsilon zeta"
    pages = paginate(text, 8, 3)
    assert render_page(pages, 0) == "alpha be\nta\ngamma\n-- page 1/3 --"
    assert render_page(pages, 2) == "ta\n-- page 3/3 --"
    try:
        render_page(pages, 3)
        assert False, "out-of-range page rendered"
    except IndexError:
        pass


# --------------------------- feature: ANSI-escape-aware width handling

def test_visible_width():
    from pager import visible_width
    assert visible_width("abc") == 3
    assert visible_width(RED + "abc" + RESET) == 3
    assert visible_width(FG256 + "X" + RESET) == 1
    assert visible_width("") == 0
    assert visible_width(RED + RESET) == 0
    assert visible_width("ab\x1b[31") == 2   # truncated sequence: zero width


def test_wrap_counts_only_visible_columns():
    assert wrap_line(RED + "abcdef" + RESET, 3) == [RED + "abc",
                                                    "def" + RESET]
    assert wrap_line("abc" + BOLD + "def", 3) == ["abc", BOLD + "def"]


def test_escape_travels_with_the_char_it_styles():
    assert wrap_line("ab" + BOLD + "cd", 2) == ["ab", BOLD + "cd"]
    assert wrap_line("a" + BOLD + "bcd", 2) == ["a" + BOLD + "b", "cd"]


def test_dense_sequences():
    line = FG256 + "he" + RESET + BOLD + "llo" + RESET
    assert wrap_line(line, 3) == [FG256 + "he" + RESET + BOLD + "l",
                                  "lo" + RESET]


def test_codes_only_line_stays_whole():
    assert wrap_line(RED + RESET, 5) == [RED + RESET]
    assert wrap_line("", 5) == [""]


def test_truncated_escape_is_safe():
    assert wrap_line("ab\x1b[31", 2) == ["ab\x1b[31"]
    assert wrap_line("abc\x1b[", 2) == ["ab", "c\x1b["]


def test_paginate_sees_visible_width():
    text = RED + "x" * 10 + RESET + "\nlogs"
    pages = paginate(text, 4, 2)
    assert pages == [
        [RED + "xxxx", "xxxx"],
        ["xx" + RESET, "logs"],
    ]
    assert page_count(text, 4, 2) == 2
    assert render_page(pages, 1).endswith("-- page 2/2 --")


EXISTING = [
    test_wrap_plain_text,
    test_dimension_validation,
    test_paginate_plain_text,
    test_render_page_footer,
]

FEATURE = [
    test_visible_width,
    test_wrap_counts_only_visible_columns,
    test_escape_travels_with_the_char_it_styles,
    test_dense_sequences,
    test_codes_only_line_stays_whole,
    test_truncated_escape_is_safe,
    test_paginate_sees_visible_width,
]


def main():
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
    main()
