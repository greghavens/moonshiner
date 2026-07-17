"""Acceptance tests for the inline markdown renderer. Run: python3 test_mdinline.py"""


def main():
    from mdinline import render_inline

    # -- plain text passes through, HTML-sensitive chars do not --
    assert render_inline("hello world") == "hello world"
    assert render_inline("") == ""
    assert render_inline('a < b & "c" > d') == "a &lt; b &amp; &quot;c&quot; &gt; d"

    # -- bold and italic --
    assert render_inline("**bold**") == "<strong>bold</strong>"
    assert render_inline("*italic*") == "<em>italic</em>"
    assert render_inline("say **it** loud") == "say <strong>it</strong> loud"
    assert render_inline("**a** and **b**") == \
        "<strong>a</strong> and <strong>b</strong>"

    # -- nesting, both directions --
    assert render_inline("**a *b* c**") == "<strong>a <em>b</em> c</strong>"
    assert render_inline("*a **b** c*") == "<em>a <strong>b</strong> c</em>"

    # -- code spans: raw contents, html-escaped, markdown ignored inside --
    assert render_inline("`x = a*b`") == "<code>x = a*b</code>"
    assert render_inline("`a<b&c>`") == "<code>a&lt;b&amp;c&gt;</code>"
    assert render_inline("`**not bold**`") == "<code>**not bold**</code>"
    assert render_inline("run `make -j4` twice") == "run <code>make -j4</code> twice"

    # -- unmatched delimiters are literal text --
    assert render_inline("**a") == "**a"
    assert render_inline("a**") == "a**"
    assert render_inline("*a") == "*a"
    assert render_inline("`a") == "`a"
    assert render_inline("**") == "**"
    assert render_inline("a``b") == "a``b", "empty code span is not a code span"

    # -- emphasis scanning must not find its closer inside a code span --
    assert render_inline("*not `emph* here`") == "*not <code>emph* here</code>"
    assert render_inline("*wraps `code*` fine*") == \
        "<em>wraps <code>code*</code> fine</em>"

    # -- backslash escapes --
    assert render_inline(r"\*six\*") == "*six*"
    assert render_inline("\\\\") == "\\"
    assert render_inline(r"\`tick\`") == "`tick`"
    assert render_inline(r"\[not a link\](x)") == "[not a link](x)"
    assert render_inline(r"path\to\file") == r"path\to\file", \
        "backslash before a non-special char stays"
    assert render_inline(r"**a \* b**") == "<strong>a * b</strong>", \
        "escaped star inside bold must not close it"

    # -- links --
    assert render_inline("[click](http://x.example)") == \
        '<a href="http://x.example">click</a>'
    assert render_inline("see [**bold** link](u) here") == \
        'see <a href="u"><strong>bold</strong> link</a> here'
    assert render_inline("[q](http://a.example?a=1&b=2)") == \
        '<a href="http://a.example?a=1&amp;b=2">q</a>'
    assert render_inline('[x](http://e.example/"quoted")') == \
        '<a href="http://e.example/&quot;quoted&quot;">x</a>'
    assert render_inline("[a](u1) then [b](u2)") == \
        '<a href="u1">a</a> then <a href="u2">b</a>'

    # -- half-formed links stay literal --
    assert render_inline("[no url]") == "[no url]"
    assert render_inline("[text](unclosed") == "[text](unclosed"
    assert render_inline("[dangling") == "[dangling"
    assert render_inline("not [a] (link)") == "not [a] (link)", \
        "the ( must touch the ]"

    # -- link text renders italic and code too --
    assert render_inline("[read *the* `code`](d)") == \
        '<a href="d">read <em>the</em> <code>code</code></a>'

    # -- kitchen sink line --
    got = render_inline(
        r"Deploy **now**: run `ship --prod`, read [*rollback* plan](https://w/r?a=1&b=2), don't \*panic\*.")
    assert got == (
        'Deploy <strong>now</strong>: run <code>ship --prod</code>, '
        'read <a href="https://w/r?a=1&amp;b=2"><em>rollback</em> plan</a>, '
        "don't *panic*."
    ), got

    print("all mdinline checks passed")


if __name__ == "__main__":
    main()
