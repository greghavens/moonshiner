"""Acceptance tests for the shared preview-markup cleaner.

Run: python3 test_htmlclean.py
"""
from htmlclean import DEFAULT_ATTRS, DEFAULT_SCHEMES, DEFAULT_TAGS, clean_html


# ---------------------------------------------------------------- text handling

def test_plain_text_is_escaped():
    assert clean_html("fees: 5 < 6 & 7 > 2") == "fees: 5 &lt; 6 &amp; 7 &gt; 2"
    assert clean_html("") == ""
    assert clean_html("  spacing\n\tkept  ") == "  spacing\n\tkept  "


def test_character_references_are_decoded_then_reescaped():
    assert clean_html("<p>Tom &amp; Jerry</p>") == "<p>Tom &amp; Jerry</p>"
    assert clean_html("<p>&lt;b&gt; is markup</p>") == "<p>&lt;b&gt; is markup</p>"
    assert clean_html("<p>caf&eacute; &#8212; open</p>") == "<p>café — open</p>"


# ---------------------------------------------------------------- tag allowlist

def test_supported_markup_passes_through():
    assert clean_html("<p>Great <b>value</b> for the price</p>") == \
        "<p>Great <b>value</b> for the price</p>"
    src = "<h2>Pros</h2><ul><li>light</li><li>quiet</li></ul>"
    assert clean_html(src) == src
    src = "<blockquote><p>quoted <em>review</em></p></blockquote>"
    assert clean_html(src) == src


def test_unsupported_tags_are_unwrapped_but_text_stays():
    assert clean_html("<div><p>keep me</p> and the tail</div>") == \
        "<p>keep me</p> and the tail"
    assert clean_html('<span class="x">plain</span>') == "plain"
    assert clean_html("<article><section><h2>T</h2><p>b</p></section></article>") == \
        "<h2>T</h2><p>b</p>"
    assert clean_html("<table><tr><td>cell one</td><td>cell two</td></tr></table>") == \
        "cell onecell two"


def test_script_and_style_bodies_are_removed_entirely():
    assert clean_html("before<script>var n = 1;</script>after") == "beforeafter"
    assert clean_html("<style>.x { color: red }</style><p>x</p>") == "<p>x</p>"
    # even when a policy lists them, their bodies are code, not prose
    assert clean_html("<script>var n = 1;</script>", tags=DEFAULT_TAGS | {"script"}) == ""


def test_comments_and_doctype_are_dropped():
    assert clean_html("<!doctype html><!-- editor note --><p>x</p>") == "<p>x</p>"
    assert clean_html("a<!-- one --><!-- two -->b") == "ab"


def test_tag_and_attribute_names_are_lowercased():
    assert clean_html('<P CLASS="intro">ok</P>') == '<p class="intro">ok</p>'
    assert clean_html("<EM>loud</EM>") == "<em>loud</em>"


# ---------------------------------------------------------------- attribute policy

def test_unsupported_attributes_are_dropped():
    assert clean_html('<p class="note" style="color:red" data-id="7" align="center">x</p>') == \
        '<p class="note">x</p>'
    # kept attributes stay in source order
    assert clean_html('<a target="_blank" href="https://shop.example/a" rel="nofollow">go</a>') == \
        '<a href="https://shop.example/a">go</a>'
    assert clean_html('<img loading="lazy" src="/th.png" alt="thumb" width="80">') == \
        '<img src="/th.png" alt="thumb" width="80">'


def test_event_handler_attributes_are_never_emitted():
    assert clean_html('<p onclick="showMore()" class="c">x</p>') == '<p class="c">x</p>'
    assert clean_html('<img src="/th.png" alt="thumb" ONLOAD="track()">') == \
        '<img src="/th.png" alt="thumb">'
    # not even when a custom policy lists one: event-handler wiring is outside
    # what the preview renderer supports
    assert clean_html('<p onselect="f()" title="t">x</p>', attrs={"*": {"onselect", "title"}}) == \
        '<p title="t">x</p>'


def test_attribute_values_are_escaped_and_double_quoted():
    src = "<p title='a \"quoted\" & <odd> value'>x</p>"
    assert clean_html(src) == '<p title="a &quot;quoted&quot; &amp; &lt;odd&gt; value">x</p>'
    # a valueless attribute renders as name=""
    assert clean_html("<p title>x</p>") == '<p title="">x</p>'
    # references inside attribute values decode, then re-escape on output
    assert clean_html('<a href="/s?a=1&amp;b=2">both</a>') == '<a href="/s?a=1&amp;b=2">both</a>'


# ---------------------------------------------------------------- URL scheme policy

def test_supported_link_schemes_pass_through():
    for src in (
        '<a href="https://shop.example/spec">spec</a>',
        '<a href="http://shop.example/spec">spec</a>',
        '<a href="mailto:care@shop.example">write us</a>',
        '<a href="/help/returns">returns</a>',
        '<a href="help.html">help</a>',
        '<a href="#reviews">reviews</a>',
        '<a href="?page=2">next</a>',
    ):
        assert clean_html(src) == src, src
    # scheme comparison ignores case; the value itself is kept as typed
    src = '<a href="HTTPS://SHOP.EXAMPLE/SPEC">spec</a>'
    assert clean_html(src) == src
    # no scheme at all counts as relative, including protocol-relative
    src = '<img src="//cdn.example/i/1.png" alt="one">'
    assert clean_html(src) == src


def test_unsupported_link_schemes_drop_the_attribute_only():
    assert clean_html('<a href="ftp://files.example/manual.pdf">manual</a>') == "<a>manual</a>"
    assert clean_html('<a href="file:///srv/export.csv">export</a>') == "<a>export</a>"
    assert clean_html('<a href="app://open/settings">settings</a>') == "<a>settings</a>"
    assert clean_html('<img src="data:image/png;base64,AAAA" alt="chart">') == '<img alt="chart">'
    # surrounding whitespace does not change the answer
    assert clean_html('<a href="  ftp://files.example/m.pdf ">m</a>') == "<a>m</a>"
    # a value urlsplit cannot parse is dropped too
    assert clean_html('<a href="http://[bad">x</a>') == "<a>x</a>"


def test_url_check_applies_wherever_href_or_src_is_allowed():
    # a custom policy may allow href on more tags; the scheme rule still runs
    assert clean_html('<p href="ftp://a/b">y</p>', attrs={"*": {"href"}}) == "<p>y</p>"
    assert clean_html('<p href="/ok">y</p>', attrs={"*": {"href"}}) == '<p href="/ok">y</p>'


# ---------------------------------------------------------------- structure repair

def test_void_elements_render_without_close_tags():
    assert clean_html("line one<br>line two<br/>end") == "line one<br>line two<br>end"
    assert clean_html("a<hr>b") == "a<hr>b"
    assert clean_html("a</br>b") == "ab"


def test_self_closing_syntax_on_a_normal_tag_opens_and_closes_it():
    assert clean_html("<p/>after") == "<p></p>after"


def test_open_elements_are_closed_at_end_of_input():
    assert clean_html("<p>a <b>bold") == "<p>a <b>bold</b></p>"
    assert clean_html("<ul><li>only") == "<ul><li>only</li></ul>"


def test_mismatched_close_tags_are_repaired():
    # closing an outer element closes everything still open inside it
    assert clean_html("<b><i>x</b> y") == "<b><i>x</i></b> y"
    # a close tag with no matching open element is ignored
    assert clean_html("a</i>b") == "ab"
    # close tags for unwrapped elements are ignored as well
    assert clean_html("<p>a<div>b</p>c</div>") == "<p>ab</p>c"


# ---------------------------------------------------------------- policy overrides

def test_custom_tag_and_scheme_policies_replace_the_defaults():
    src = "<section><p>x</p></section>"
    assert clean_html(src, tags=DEFAULT_TAGS | {"section"}) == src
    assert clean_html('<a href="http://shop.example/x">x</a>', schemes={"https"}) == "<a>x</a>"
    assert clean_html('<a href="https://shop.example/x">x</a>', schemes={"https"}) == \
        '<a href="https://shop.example/x">x</a>'
    # the attrs mapping you pass is the whole policy; "*" applies to every tag
    assert clean_html('<p class="c" title="t">x</p>', attrs={"*": {"title"}}) == '<p title="t">x</p>'
    assert clean_html('<a href="/x" title="t">x</a>', attrs={"a": {"href"}}) == '<a href="/x">x</a>'


def test_default_policy_exports():
    assert "a" in DEFAULT_TAGS and "img" in DEFAULT_TAGS and "blockquote" in DEFAULT_TAGS
    assert "script" not in DEFAULT_TAGS and "style" not in DEFAULT_TAGS
    assert DEFAULT_ATTRS["a"] == {"href"}
    assert DEFAULT_ATTRS["img"] == {"src", "alt", "width", "height"}
    assert DEFAULT_ATTRS["*"] == {"class", "title"}
    assert set(DEFAULT_SCHEMES) == {"http", "https", "mailto"}


# ---------------------------------------------------------------- everything at once

def test_full_review_fragment():
    src = (
        "<!-- moderation queue #4821 -->"
        '<div class="review">'
        "<h2 onmouseover=\"peek()\">Solid <SCRIPT>track();</SCRIPT>kettle</h2>"
        '<p style="font-size:30px">Boils fast &amp; quiet. <a href="ftp://files.example/manual">manual</a> '
        'or <a href="https://shop.example/kettle" target="_blank">product page</a></p>'
        '<img src="//cdn.example/kettle.png" alt="the kettle" onerror="beacon()">'
        "<p>three stars"
        "</div>"
    )
    assert clean_html(src) == (
        "<h2>Solid kettle</h2>"
        "<p>Boils fast &amp; quiet. <a>manual</a> "
        'or <a href="https://shop.example/kettle">product page</a></p>'
        '<img src="//cdn.example/kettle.png" alt="the kettle">'
        "<p>three stars</p>"
    )


def main():
    tests = [
        test_plain_text_is_escaped,
        test_character_references_are_decoded_then_reescaped,
        test_supported_markup_passes_through,
        test_unsupported_tags_are_unwrapped_but_text_stays,
        test_script_and_style_bodies_are_removed_entirely,
        test_comments_and_doctype_are_dropped,
        test_tag_and_attribute_names_are_lowercased,
        test_unsupported_attributes_are_dropped,
        test_event_handler_attributes_are_never_emitted,
        test_attribute_values_are_escaped_and_double_quoted,
        test_supported_link_schemes_pass_through,
        test_unsupported_link_schemes_drop_the_attribute_only,
        test_url_check_applies_wherever_href_or_src_is_allowed,
        test_void_elements_render_without_close_tags,
        test_self_closing_syntax_on_a_normal_tag_opens_and_closes_it,
        test_open_elements_are_closed_at_end_of_input,
        test_mismatched_close_tags_are_repaired,
        test_custom_tag_and_scheme_policies_replace_the_defaults,
        test_default_policy_exports,
        test_full_review_fragment,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
