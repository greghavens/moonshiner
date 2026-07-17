"""Acceptance tests for the plain-text table formatter. Run: python3 test_texttable.py"""


def main():
    from texttable import format_table

    # -- basic layout: widths from content, ' | ' gutters, dashed separator --
    out = format_table(
        [["apple", 12, 0.5], ["banana", 3, 1.25]],
        ["name", "qty", "price"],
        align=["l", "r", "r"],
    )
    assert out == (
        "name   | qty | price\n"
        "-------+-----+------\n"
        "apple  |  12 |   0.5\n"
        "banana |   3 |  1.25"
    ), repr(out)

    # -- default alignment is left for every column --
    out = format_table([["a", "bb"]], ["x", "y"])
    assert out == (
        "x | y\n"
        "--+---\n"
        "a | bb"
    ), repr(out)

    # -- header can be the widest thing in a column --
    out = format_table([["hi", "1"]], ["greeting", "n"])
    assert out == (
        "greeting | n\n"
        "---------+--\n"
        "hi       | 1"
    ), repr(out)

    # -- no trailing whitespace on any line, ever --
    out = format_table([["a", "b"], ["longer", "c"]], ["col1", "col2"])
    for line in out.splitlines():
        assert line == line.rstrip(), f"trailing whitespace in {line!r}"

    # -- center alignment: str.center semantics, trailing pad stripped --
    out = format_table([["ab"]], ["wide!"], align=["c"])
    assert out.splitlines()[2] == "ab".center(5).rstrip(), repr(out)
    out = format_table([["abc"], ["a"]], ["colname"], align=["c"])
    lines = out.splitlines()
    assert lines[2] == "  abc", repr(out)
    assert lines[3] == "   a", repr(out)

    # -- non-string cells go through str(); None renders as empty --
    out = format_table([[None, 42]], ["a", "b"])
    assert out.splitlines()[2] == "  | 42", repr(out)

    # -- empty row list: header and separator only --
    out = format_table([], ["id", "name"])
    assert out == (
        "id | name\n"
        "---+-----"
    ), repr(out)

    # -- short rows are padded with empty cells --
    out = format_table([["only"]], ["a", "b"])
    assert out.splitlines()[2] == "only", repr(out)

    # -- rows longer than the header are an error --
    try:
        format_table([["a", "b", "c"]], ["x", "y"])
        assert False, "expected ValueError for oversized row"
    except ValueError:
        pass

    # -- max_col_width wraps cells at word boundaries --
    out = format_table(
        [["1", "the quick brown fox jumps"], ["2", "ok"]],
        ["id", "comment"],
        max_col_width=10,
    )
    assert out == (
        "id | comment\n"
        "---+----------\n"
        "1  | the quick\n"
        "   | brown fox\n"
        "   | jumps\n"
        "2  | ok"
    ), repr(out)

    # -- a single word longer than the cap is hard-broken --
    out = format_table([["supercalifragilistic"]], ["w"], max_col_width=6)
    assert out == (
        "w\n"
        "------\n"
        "superc\n"
        "alifra\n"
        "gilist\n"
        "ic"
    ), repr(out)

    # -- wrapping never shrinks a column below its header width --
    out = format_table([["aaaa bbbb"]], ["longheader"], max_col_width=4)
    lines = out.splitlines()
    assert lines[0] == "longheader", repr(out)
    assert lines[1] == "----------", repr(out)
    assert lines[2] == "aaaa", repr(out)
    assert lines[3] == "bbbb", repr(out)

    # -- two wrapped cells in one row line up on continuation lines --
    out = format_table(
        [["alpha beta gamma", "one two"]],
        ["l", "r"],
        max_col_width=5,
        align=["l", "r"],
    )
    assert out == (
        "l     |   r\n"
        "------+----\n"
        "alpha | one\n"
        "beta  | two\n"
        "gamma"
    ), repr(out)

    # -- alignment list must match the header count --
    try:
        format_table([["a"]], ["x"], align=["l", "r"])
        assert False, "expected ValueError for align length mismatch"
    except ValueError:
        pass

    # -- alignment codes are only l, r, c --
    try:
        format_table([["a"]], ["x"], align=["m"])
        assert False, "expected ValueError for bad align code"
    except ValueError:
        pass

    print("all texttable checks passed")


if __name__ == "__main__":
    main()
