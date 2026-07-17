"""Acceptance checks for slides.py. Run: python3 test_slides.py"""
import slides as mod
from slides import split_slides

DECK = """\
# Welcome

Hello everyone.
---

## Agenda

- intro
- demo
---
No heading here
just prose
"""


# ---------------------------------------------------------------- existing

def test_splits_on_delimiter_and_extracts_titles():
    out = split_slides(DECK)
    assert len(out) == 3, out
    assert [s["title"] for s in out] == ["Welcome", "Agenda", None]
    assert out[0]["body"] == "Hello everyone."
    assert out[1]["body"] == "- intro\n- demo"
    assert out[2]["body"] == "No heading here\njust prose"


def test_delimiter_must_be_the_whole_line():
    text = "# One\n\nabove\n-----\nbelow\n --- \nend"
    out = split_slides(text)
    assert len(out) == 1, out
    assert out[0]["body"] == "above\n-----\nbelow\n --- \nend"


def test_blank_padding_trimmed_and_empty_slides_dropped():
    text = "\n\n# A\n\nbody\n\n---\n\n   \n---\n# B\n"
    out = split_slides(text)
    assert [s["title"] for s in out] == ["A", "B"]
    assert out[0]["body"] == "body"
    assert out[1]["body"] == ""


def test_only_first_heading_becomes_the_title():
    out = split_slides("# A\n\n## Sub\ntext")
    assert len(out) == 1
    assert out[0]["title"] == "A"
    assert out[0]["body"] == "## Sub\ntext"


def test_hash_without_space_is_not_a_heading():
    out = split_slides("#hashtag\nbody")
    assert out[0]["title"] is None
    assert out[0]["body"] == "#hashtag\nbody"


# ----------------------------------------------------------------- feature

def test_notes_extracted_and_removed_from_body():
    out = split_slides("# T\n\npoint\n\nNote: remember to breathe\nand smile")
    assert len(out) == 1
    assert out[0]["body"] == "point"
    assert out[0]["notes"] == "remember to breathe\nand smile"


def test_slides_without_notes_get_empty_string():
    out = split_slides(DECK)
    assert all(s["notes"] == "" for s in out)


def test_note_marker_must_start_the_line():
    out = split_slides("# T\n\nSee Note: below for details")
    assert out[0]["body"] == "See Note: below for details"
    assert out[0]["notes"] == ""


def test_fragments_numbered_per_slide_and_marker_stripped():
    text = ("# One\n\n- a <!-- fragment -->\n- b\n- c <!-- fragment -->\n"
            "---\n# Two\n\nsteps:\n  1. pour <!-- fragment -->\n"
            "  2. stir <!-- fragment -->")
    out = split_slides(text)
    assert out[0]["fragments"] == [(1, "- a"), (2, "- c")], out[0]["fragments"]
    assert out[0]["body"] == "- a\n- b\n- c"
    assert out[1]["fragments"] == [(1, "1. pour"), (2, "2. stir")]
    assert out[1]["body"] == "steps:\n  1. pour\n  2. stir"


def test_slides_without_fragments_get_empty_list():
    out = split_slides(DECK)
    assert all(s["fragments"] == [] for s in out)


def test_fragment_marker_inside_notes_stays_verbatim():
    out = split_slides("# T\n\nbody line\nNote: watch timing <!-- fragment -->")
    assert out[0]["fragments"] == []
    assert out[0]["notes"] == "watch timing <!-- fragment -->"
    assert out[0]["body"] == "body line"


def test_toc_slide_lists_titled_slides_only():
    deck = split_slides("# Alpha\n\na\n---\nuntitled prose\n---\n# Gamma\n\ng")
    toc = mod.toc_slide(deck)
    assert toc == {"title": "Contents", "body": "1. Alpha\n2. Gamma",
                   "notes": "", "fragments": []}, toc


def test_toc_slide_accepts_a_custom_title():
    deck = split_slides("# Alpha\n\na")
    toc = mod.toc_slide(deck, title="In this talk")
    assert toc["title"] == "In this talk"
    assert toc["body"] == "1. Alpha"


def test_split_with_toc_prepends_a_contents_slide():
    plain = split_slides(DECK)
    with_toc = split_slides(DECK, toc=True)
    assert len(with_toc) == len(plain) + 1
    assert with_toc[0]["title"] == "Contents"
    assert with_toc[0]["body"] == "1. Welcome\n2. Agenda"
    assert with_toc[1:] == plain


EXISTING = [
    test_splits_on_delimiter_and_extracts_titles,
    test_delimiter_must_be_the_whole_line,
    test_blank_padding_trimmed_and_empty_slides_dropped,
    test_only_first_heading_becomes_the_title,
    test_hash_without_space_is_not_a_heading,
]

FEATURE = [
    test_notes_extracted_and_removed_from_body,
    test_slides_without_notes_get_empty_string,
    test_note_marker_must_start_the_line,
    test_fragments_numbered_per_slide_and_marker_stripped,
    test_slides_without_fragments_get_empty_list,
    test_fragment_marker_inside_notes_stays_verbatim,
    test_toc_slide_lists_titled_slides_only,
    test_toc_slide_accepts_a_custom_title,
    test_split_with_toc_prepends_a_contents_slide,
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
