"""Acceptance tests for the handbook accessibility auditor.

Fixtures are built as line lists so the expected line numbers stay obvious:
line N is PAGE[N-1].

Run: python3 test_a11y_audit.py
"""
from a11y_audit import audit


def page(*lines):
    return "\n".join(lines)


# ---------------------------------------------------------------- clean pages

def test_clean_page_has_no_findings():
    src = page(
        "<html>",                                        # 1
        "<head><title>Team handbook</title></head>",     # 2
        "<body>",                                        # 3
        "<header><h1>Handbook</h1></header>",            # 4
        '<main id="content">',                           # 5
        "<h2>Getting started</h2>",                      # 6
        '<img src="/img/badge.png" alt="staff badge">',  # 7
        "<form>",                                        # 8
        '<label for="email">Work email</label>',         # 9
        '<input id="email" type="email">',               # 10
        "</form>",                                       # 11
        "<h3>First week</h3>",                           # 12
        "</main>",                                       # 13
        "</body>",                                       # 14
        "</html>",                                       # 15
    )
    assert audit(src) == []


# ---------------------------------------------------------------- heading levels

def test_first_heading_must_be_h1():
    src = page(
        "<main>",          # 1
        "<h2>Intro</h2>",  # 2
        "</main>",         # 3
    )
    assert audit(src) == [("heading-skip", 2, "h2")]


def test_heading_level_skip_is_flagged_where_it_happens():
    src = page(
        "<main>",             # 1
        "<h1>Title</h1>",     # 2
        "<h3>Details</h3>",   # 3
        "</main>",            # 4
    )
    assert audit(src) == [("heading-skip", 3, "h3")]


def test_descending_and_single_step_headings_are_fine():
    src = page(
        "<main>",       # 1
        "<h1>A</h1>",   # 2
        "<h2>B</h2>",   # 3
        "<h3>C</h3>",   # 4
        "<h2>D</h2>",   # 5
        "<h3>E</h3>",   # 6
        "<h1>F</h1>",   # 7
        "<h2>G</h2>",   # 8
        "</main>",      # 9
    )
    assert audit(src) == []


def test_skip_relative_to_the_previous_heading_not_the_max():
    src = page(
        "<main>",      # 1
        "<h1>A</h1>",  # 2
        "<h2>B</h2>",  # 3
        "<h1>C</h1>",  # 4
        "<h3>D</h3>",  # 5
        "</main>",     # 6
    )
    assert audit(src) == [("heading-skip", 5, "h3")]


# ---------------------------------------------------------------- image alt text

def test_images_need_an_alt_attribute_empty_is_fine():
    src = page(
        "<main>",                              # 1
        '<img src="/i/one.png" alt="one">',    # 2
        '<img src="/i/two.png" alt="">',       # 3
        '<img src="/i/three.png">',            # 4
        '<img src="/i/four.png"/>',            # 5
        "<img>",                               # 6
        "</main>",                             # 7
    )
    assert audit(src) == [
        ("img-missing-alt", 4, "/i/three.png"),
        ("img-missing-alt", 5, "/i/four.png"),
        ("img-missing-alt", 6, ""),
    ]


def test_findings_point_at_the_line_where_the_tag_opens():
    src = page(
        "<main>",                  # 1
        "<img",                    # 2
        '    src="/i/tall.png">',  # 3
        "</main>",                 # 4
    )
    assert audit(src) == [("img-missing-alt", 2, "/i/tall.png")]


# ---------------------------------------------------------------- label wiring

def test_labels_must_reference_a_form_control_id():
    src = page(
        "<main>",                                    # 1
        '<label for="email">Email</label>',          # 2
        '<input id="email">',                        # 3
        '<label for="phone">Phone</label>',          # 4
        "<label>bare labels are fine</label>",       # 5
        '<label for="bio">Bio</label>',              # 6
        '<textarea id="bio"></textarea>',            # 7
        '<label for="team">Team</label>',            # 8
        '<select id="team"></select>',               # 9
        '<label for="note">Note</label>',            # 10
        '<div id="note">not a form control</div>',   # 11
        "</main>",                                   # 12
    )
    assert audit(src) == [
        ("label-orphan", 4, "phone"),
        ("label-orphan", 10, "note"),
    ]


# ---------------------------------------------------------------- duplicate ids

def test_each_repeat_of_an_id_is_flagged():
    src = page(
        "<main>",                              # 1
        '<p id="intro">a</p>',                 # 2
        '<p id="intro">b</p>',                 # 3
        '<span id="badge">c</span>',           # 4
        '<em id="intro">d</em>',               # 5
        '<b id="">empty ids do not count</b>', # 6
        '<i id="">empty ids do not count</i>', # 7
        '<u id="Intro">case matters</u>',      # 8
        "</main>",                             # 9
    )
    assert audit(src) == [
        ("duplicate-id", 3, "intro"),
        ("duplicate-id", 5, "intro"),
    ]


# ---------------------------------------------------------------- landmark

def test_missing_main_landmark_is_reported_at_line_one():
    assert audit("<body><h1>T</h1></body>") == [("landmark-missing", 1, "main")]
    assert audit("") == [("landmark-missing", 1, "main")]


def test_a_main_element_satisfies_the_landmark_check():
    assert audit("<main><h1>T</h1></main>") == []


# ---------------------------------------------------------------- everything at once

def test_findings_are_sorted_by_line_then_code_then_subject():
    src = page(
        "<body>",                                              # 1
        "<h2>Release notes</h2>",                              # 2
        '<img src="/z.png"><img src="/a.png"><h4>deep</h4>',   # 3
        '<label for="q">Search</label>',                       # 4
        '<p id="x">one</p><p id="x">two</p>',                  # 5
        "</body>",                                             # 6
    )
    findings = audit(src)
    assert findings == [
        ("landmark-missing", 1, "main"),
        ("heading-skip", 2, "h2"),
        ("heading-skip", 3, "h4"),
        ("img-missing-alt", 3, "/a.png"),
        ("img-missing-alt", 3, "/z.png"),
        ("label-orphan", 4, "q"),
        ("duplicate-id", 5, "x"),
    ]
    for finding in findings:
        assert len(finding) == 3
        assert isinstance(finding[0], str)
        assert isinstance(finding[1], int)
        assert isinstance(finding[2], str)


def main():
    tests = [
        test_clean_page_has_no_findings,
        test_first_heading_must_be_h1,
        test_heading_level_skip_is_flagged_where_it_happens,
        test_descending_and_single_step_headings_are_fine,
        test_skip_relative_to_the_previous_heading_not_the_max,
        test_images_need_an_alt_attribute_empty_is_fine,
        test_findings_point_at_the_line_where_the_tag_opens,
        test_labels_must_reference_a_form_control_id,
        test_each_repeat_of_an_id_is_flagged,
        test_missing_main_landmark_is_reported_at_line_one,
        test_a_main_element_satisfies_the_landmark_check,
        test_findings_are_sorted_by_line_then_code_then_subject,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
