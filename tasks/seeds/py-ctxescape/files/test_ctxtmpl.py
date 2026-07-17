"""Acceptance tests for the context-aware template renderer.

Run: python3 test_ctxtmpl.py
"""
from ctxtmpl import (
    TemplateContextError,
    TemplateSyntaxError,
    placeholder_contexts,
    render,
)


# ---------------------------------------------------------------- text context

def test_text_context_html_escapes_the_value():
    out = render("Hello {{ who }}!", {"who": "Ops & Dev <team>"})
    assert out == "Hello Ops &amp; Dev &lt;team&gt;!", out


def test_static_template_text_is_emitted_verbatim():
    out = render("<p>a & b <b>{{ x }}</b></p>", {"x": "c"})
    assert out == "<p>a & b <b>c</b></p>", out


def test_values_are_coerced_with_str():
    assert render("{{ n }} items", {"n": 3}) == "3 items"


def test_placeholder_whitespace_forms():
    assert render("{{x}}|{{  x  }}", {"x": "v"}) == "v|v"


# ---------------------------------------------------------------- attribute context

def test_attribute_context_escapes_quotes_too():
    out = render('<div title="{{ tip }}">x</div>', {"tip": 'say "hi" & <wave>'})
    assert out == '<div title="say &quot;hi&quot; &amp; &lt;wave&gt;">x</div>', out


def test_single_quoted_attributes_are_supported():
    out = render("<div title='{{ tip }}'>x</div>", {"tip": "it's fine"})
    assert out == "<div title='it&#x27;s fine'>x</div>", out


def test_gt_inside_a_static_attribute_does_not_end_the_tag():
    assert placeholder_contexts('<div title="x > y">{{ a }}</div>') == [("a", "text")]


# ---------------------------------------------------------------- URL contexts

def test_full_url_position_with_supported_scheme_is_kept():
    out = render('<a href="{{ link }}">run</a>',
                 {"link": "https://ops.example/run?a=1&b=2"})
    assert out == '<a href="https://ops.example/run?a=1&amp;b=2">run</a>', out
    out = render('<a href="{{ link }}">top</a>', {"link": "#top"})
    assert out == '<a href="#top">top</a>', out
    out = render('<img src="{{ pic }}">', {"pic": "/charts/latest.png"})
    assert out == '<img src="/charts/latest.png">', out
    out = render('<a href="{{ link }}">mail</a>', {"link": "mailto:oncall@ops.example"})
    assert out == '<a href="mailto:oncall@ops.example">mail</a>', out
    # the scheme comparison ignores case
    out = render('<a href="{{ link }}">x</a>', {"link": "HTTPS://OPS.EXAMPLE/A"})
    assert out == '<a href="HTTPS://OPS.EXAMPLE/A">x</a>', out


def test_full_url_position_with_unsupported_scheme_renders_a_hash():
    for link in ("ftp://files.example/x", "data:text/plain,hello",
                 "app://open", "  ftp://files.example/x"):
        out = render('<a href="{{ link }}">x</a>', {"link": link})
        assert out == '<a href="#">x</a>', (link, out)


def test_url_component_position_is_percent_encoded():
    out = render('<a href="/redirect?next={{ next }}">go</a>',
                 {"next": "/deep path?x=1&y=2"})
    assert out == '<a href="/redirect?next=%2Fdeep%20path%3Fx%3D1%26y%3D2">go</a>', out
    out = render('<img src="/thumb/{{ id }}.png">', {"id": "rev 12"})
    assert out == '<img src="/thumb/rev%2012.png">', out
    # non-ascii encodes as utf-8 bytes
    out = render('<img src="/u/{{ name }}.png">', {"name": "café"})
    assert out == '<img src="/u/caf%C3%A9.png">', out


def test_second_placeholder_in_a_url_attribute_is_a_component():
    out = render('<a href="{{ base }}/item/{{ slug }}">x</a>',
                 {"base": "https://shop.example", "slug": "mugs & more"})
    assert out == '<a href="https://shop.example/item/mugs%20%26%20more">x</a>', out


def test_url_attributes_on_any_tag_including_script_src():
    out = render('<script src="{{ s }}"></script>', {"s": "/static/app.js"})
    assert out == '<script src="/static/app.js"></script>', out


# ---------------------------------------------------------------- one value, three sinks

def test_same_value_escapes_differently_per_context():
    tmpl = '<p data-q="{{ q }}">{{ q }}<a href="/s?q={{ q }}">find</a></p>'
    out = render(tmpl, {"q": 'a "b" & <c>'})
    assert out == ('<p data-q="a &quot;b&quot; &amp; &lt;c&gt;">'
                   'a "b" &amp; &lt;c&gt;'
                   '<a href="/s?q=a%20%22b%22%20%26%20%3Cc%3E">find</a></p>'), out


# ---------------------------------------------------------------- context inference report

def test_placeholder_contexts_lists_each_use_in_order():
    tmpl = ('<p title="{{ a }}">{{ b }}</p>'
            '<a href="{{ c }}">x</a>'
            '<img src="/i/{{ d }}.png">'
            '{{ b }}')
    assert placeholder_contexts(tmpl) == [
        ("a", "attr"),
        ("b", "text"),
        ("c", "url-full"),
        ("d", "url-part"),
        ("b", "text"),
    ]


def test_text_after_a_script_block_is_text_again():
    assert placeholder_contexts("<script>var x = 1;</script>{{ ok }}") == [("ok", "text")]


# ---------------------------------------------------------------- unsupported contexts

def expect_context_error(source, name, where):
    for fn in (lambda: render(source, {}), lambda: placeholder_contexts(source)):
        try:
            fn()
        except TemplateContextError as err:
            assert err.name == name, (err.name, name)
            assert err.where == where, (err.where, where)
        else:
            raise AssertionError(f"expected TemplateContextError for {source!r}")


def test_placeholder_inside_a_tag_is_rejected():
    expect_context_error("<{{ tag }}>x</div>", "tag", "tag")
    expect_context_error('<div {{ attr }}="v">x</div>', "attr", "tag")


def test_placeholder_in_an_unquoted_attribute_value_is_rejected():
    expect_context_error("<div class={{ cls }}>x</div>", "cls", "unquoted-attr")


def test_placeholder_in_script_or_style_bodies_is_rejected():
    expect_context_error("<script>var x = {{ data }};</script>", "data", "script-style")
    expect_context_error("<style>.x { color: {{ c }} }</style>", "c", "script-style")
    expect_context_error("<SCRIPT>{{ d }}</SCRIPT>", "d", "script-style")


def test_placeholder_inside_a_comment_is_rejected():
    expect_context_error("<!-- note {{ n }} -->", "n", "comment")


def test_context_errors_do_not_depend_on_values():
    # the scan happens before any lookup: no KeyError, the context error wins
    try:
        render("<script>{{ missing }}</script>", {})
    except TemplateContextError as err:
        assert err.where == "script-style"
    else:
        raise AssertionError("expected TemplateContextError")


# ---------------------------------------------------------------- syntax and lookup errors

def test_malformed_placeholders_raise_syntax_errors():
    for source in ("{{ name", "{{ }}", "{{ user.name }}", "{{ 9lives }}", "pre {{"):
        try:
            render(source, {"name": "x"})
        except TemplateSyntaxError:
            pass
        else:
            raise AssertionError(f"expected TemplateSyntaxError for {source!r}")


def test_missing_values_raise_keyerror():
    try:
        render("{{ absent }}", {})
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def main():
    tests = [
        test_text_context_html_escapes_the_value,
        test_static_template_text_is_emitted_verbatim,
        test_values_are_coerced_with_str,
        test_placeholder_whitespace_forms,
        test_attribute_context_escapes_quotes_too,
        test_single_quoted_attributes_are_supported,
        test_gt_inside_a_static_attribute_does_not_end_the_tag,
        test_full_url_position_with_supported_scheme_is_kept,
        test_full_url_position_with_unsupported_scheme_renders_a_hash,
        test_url_component_position_is_percent_encoded,
        test_second_placeholder_in_a_url_attribute_is_a_component,
        test_url_attributes_on_any_tag_including_script_src,
        test_same_value_escapes_differently_per_context,
        test_placeholder_contexts_lists_each_use_in_order,
        test_text_after_a_script_block_is_text_again,
        test_placeholder_inside_a_tag_is_rejected,
        test_placeholder_in_an_unquoted_attribute_value_is_rejected,
        test_placeholder_in_script_or_style_bodies_is_rejected,
        test_placeholder_inside_a_comment_is_rejected,
        test_context_errors_do_not_depend_on_values,
        test_malformed_placeholders_raise_syntax_errors,
        test_missing_values_raise_keyerror,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
