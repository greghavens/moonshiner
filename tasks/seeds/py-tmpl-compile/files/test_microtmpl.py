"""Acceptance checks for microtmpl.py. Run: python3 test_microtmpl.py"""
from microtmpl import compile_template, TemplateSyntaxError, RenderError


def test_substitution_and_dot_paths():
    render = compile_template(
        "Hi {{ user.name }}, order {{ order.id }} ships to "
        "{{ order.items.0.city }}.")
    out = render({
        "user": {"name": "Ada"},
        "order": {"id": 7001, "items": [{"city": "Oslo"}]},
    })
    assert out == "Hi Ada, order 7001 ships to Oslo.", out


def test_digit_segment_against_dict_is_a_key():
    render = compile_template("total {{ stats.2026.total }}")
    assert render({"stats": {"2026": {"total": 42}}}) == "total 42"
    render = compile_template("first {{ rows.0 }}")
    assert render({"rows": ["alpha", "beta"]}) == "first alpha"


def test_filters_chain_left_to_right():
    render = compile_template("{{ name | upper }}/{{ name | title }}/"
                              "{{ shout | lower }}")
    assert render({"name": "ada lovelace", "shout": "HEY"}) == \
        "ADA LOVELACE/Ada Lovelace/hey"
    render = compile_template("{{ desc | trunc:5 | upper }}")
    assert render({"desc": "abcdefgh"}) == "ABCDE"
    render = compile_template("{{ desc | trunc:99 }}")
    assert render({"desc": "short"}) == "short"


def test_default_filter_semantics():
    render = compile_template("{{ nick | default:anon | upper }}")
    assert render({}) == "ANON"
    assert render({"nick": None}) == "ANON"
    assert render({"nick": "grace"}) == "GRACE"
    # filters BEFORE default are skipped for a missing value
    render = compile_template("{{ nick | upper | default:anon }}")
    assert render({}) == "anon"
    assert render({"nick": "grace"}) == "GRACE"


def test_render_errors_carry_the_path():
    render = compile_template("Hi {{ user.name }}")
    for ctx in [{}, {"user": {}}, {"user": {"name": None}},
                {"user": "flat string"}]:
        try:
            render(ctx)
            assert False, "rendered %r" % (ctx,)
        except RenderError as e:
            assert e.path == "user.name", e.path
    render = compile_template("{{ rows.5 }}")
    try:
        render({"rows": ["only one"]})
        assert False, "index out of range rendered"
    except RenderError as e:
        assert e.path == "rows.5"


def test_syntax_errors_report_position():
    cases = [
        ("hello {{ name", 1, 7),                      # unclosed
        ("{{ }}", 1, 1),                              # empty tag
        ("ok\nline two {{ x | bogus }}", 2, 10),      # unknown filter
        ("a\nb\nc {{ d | trunc }}", 3, 3),            # trunc needs an arg
        ("{{ d | trunc:many }}", 1, 1),               # ...an integer arg
        ("{{ name | upper:5 }}", 1, 1),               # upper takes no arg
        ("{{ name | default }}", 1, 1),               # bare default
    ]
    for source, line, col in cases:
        try:
            compile_template(source)
            assert False, "compiled %r" % (source,)
        except TemplateSyntaxError as e:
            assert (e.line, e.col) == (line, col), \
                "%r -> %r" % (source, (e.line, e.col))


def test_compile_time_means_before_render():
    try:
        compile_template("{{ x | bogus }}")
        assert False, "bad filter survived compilation"
    except TemplateSyntaxError:
        pass


def test_plain_text_and_lone_braces_pass_through():
    render = compile_template("no tags here")
    assert render({}) == "no tags here"
    render = compile_template("a { b } c }} d")
    assert render({}) == "a { b } c }} d"
    assert compile_template("")({}) == ""


def test_render_is_reusable_and_stateless():
    render = compile_template("{{ greeting }}, {{ who | title }}!")
    assert render({"greeting": "hello", "who": "world"}) == "hello, World!"
    assert render({"greeting": "bye", "who": "moon"}) == "bye, Moon!"
    assert render({"greeting": "hello", "who": "world"}) == "hello, World!"


def test_non_string_values_are_stringified():
    render = compile_template("{{ n }} + {{ f }} = {{ ok }}")
    assert render({"n": 2, "f": 2.5, "ok": True}) == "2 + 2.5 = True"


CHECKS = [
    test_substitution_and_dot_paths,
    test_digit_segment_against_dict_is_a_key,
    test_filters_chain_left_to_right,
    test_default_filter_semantics,
    test_render_errors_carry_the_path,
    test_syntax_errors_report_position,
    test_compile_time_means_before_render,
    test_plain_text_and_lone_braces_pass_through,
    test_render_is_reusable_and_stateless,
    test_non_string_values_are_stringified,
]


def main():
    failures = 0
    checks = CHECKS
    for t in checks:
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
    print("\nall %d checks passed" % len(checks))


if __name__ == "__main__":
    main()
