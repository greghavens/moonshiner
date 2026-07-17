"""Acceptance tests for the wrap/justify engine. Run: python3 test_justwrap.py"""


def main():
    from justwrap import wrap, justify

    # -- basic greedy wrapping --
    lines = wrap("The quick brown fox jumps over the lazy dog", 16)
    assert lines == ["The quick brown", "fox jumps over", "the lazy dog"], lines
    assert all(len(l) <= 16 for l in lines)

    # a word goes on the current line only if it fits with its separator space
    assert wrap("hello world", 11) == ["hello world"]     # exactly 11
    assert wrap("hello world", 10) == ["hello", "world"]  # 11 > 10

    # single word narrower than width
    assert wrap("hi", 40) == ["hi"]

    # -- whitespace collapsing: tabs, newlines, runs of spaces are all one gap --
    assert wrap("a\tb\nc", 10) == ["a b c"]
    assert wrap("  spaced    out\n\n  text  ", 20) == ["spaced out text"]

    # -- empty input --
    assert wrap("", 10) == []
    assert wrap("   \n\t ", 10) == []
    assert justify("", 10) == []
    assert justify(" \n ", 10) == []

    # -- width validation --
    for bad_width in (0, -2):
        for fn in (wrap, justify):
            try:
                fn("x", bad_width)
                assert False, f"{fn.__name__} should reject width={bad_width}"
            except ValueError:
                pass

    # -- a word with no hyphens is never split, even if it overflows --
    lines = wrap("a extraordinarily b", 5)
    assert lines == ["a", "extraordinarily", "b"], lines

    # -- hyphenated words may break after a hyphen to use remaining space --
    lines = wrap("the well-known state-of-the-art solution", 12)
    assert lines == ["the well-", "known state-", "of-the-art", "solution"], lines

    # when not even the shortest hyphen prefix fits in the remaining room,
    # the whole word moves down unbroken
    lines = wrap("hello well-known", 10)
    assert lines == ["hello", "well-known"], lines

    # on a fresh line an over-long hyphenated word breaks greedily,
    # longest hyphen-terminated prefix first
    lines = wrap("multi-part-name", 6)
    assert lines == ["multi-", "part-", "name"], lines

    # a trailing hyphen is not a break opportunity (nothing may be left behind)
    assert wrap("re-do", 4) == ["re-", "do"]

    # -- justify: every line except the last is padded to exactly the width --
    lines = justify("The quick brown fox jumps over the lazy dog", 16)
    assert lines == ["The  quick brown", "fox  jumps  over", "the lazy dog"], lines
    for l in lines[:-1]:
        assert len(l) == 16, l

    # extra spaces go to the leftmost gaps first
    lines = justify("ab cd ef gh xxxx", 15)
    assert lines == ["ab   cd  ef  gh", "xxxx"], lines

    # single space between words when the count divides evenly
    lines = justify("aa bb cc dd ee", 7)
    assert lines == ["aa   bb", "cc   dd", "ee"], lines

    # -- the last line is left-aligned, never padded --
    lines = justify("one two three four five six", 12)
    assert lines[-1] == lines[-1].rstrip()
    assert "  " not in lines[-1]

    # -- a line holding a single word is left as-is, not padded --
    lines = justify("a extraordinarily b tail words here", 15)
    assert lines == ["a", "extraordinarily", "b   tail  words", "here"], lines

    # -- justify pads hyphen-broken lines like any other --
    lines = justify("the well-known state-of-the-art solution", 12)
    assert lines == ["the    well-", "known state-", "of-the-art", "solution"], lines

    # justified output round-trips: stripping padding recovers the wrap
    text = "Better three hours too soon than a minute too late says the bard"
    wrapped = wrap(text, 21)
    just = justify(text, 21)
    assert len(wrapped) == len(just)
    for w, j in zip(wrapped, just):
        assert " ".join(j.split()) == " ".join(w.split()), (w, j)
    for j in just[:-1]:
        if len(j.split()) > 1:
            assert len(j) == 21, j

    # wrap/justify never mutate their input relationship: words keep order
    flat = " ".join(justify(text, 21)).split()
    assert flat == text.split(), flat

    print("ok")


if __name__ == "__main__":
    main()
