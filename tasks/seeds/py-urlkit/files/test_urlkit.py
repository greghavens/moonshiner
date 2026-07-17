"""Acceptance tests for the URL toolkit. Run: python3 test_urlkit.py"""


def expect_value_error(fn, *args):
    try:
        fn(*args)
    except ValueError:
        return
    raise AssertionError(f"{fn.__name__}{args!r} should raise ValueError")


def main():
    import urlkit
    from urlkit import parse, normalize_path

    # the whole point of this module is to NOT lean on urllib
    import sys
    assert "urllib.parse" not in sys.modules, "urlkit must not import urllib.parse"

    # -- parsing a full URL --
    u = parse("https://Example.COM:8443/a/b?q=hello+world&tag=caf%C3%A9#top")
    assert u.scheme == "https"
    assert u.host == "example.com"          # host is case-insensitive: lowercased
    assert u.port == 8443
    assert u.path == "/a/b"
    assert u.query == [("q", "hello world"), ("tag", "café")]
    assert u.fragment == "top"
    assert str(u) == "https://example.com:8443/a/b?q=hello+world&tag=caf%C3%A9#top"

    # scheme is lowercased too
    u = parse("HTTP://example.com/x")
    assert u.scheme == "http"
    assert str(u).startswith("http://")

    # -- no port, no query, no fragment --
    u = parse("http://example.com/index.html")
    assert u.port is None and u.query == [] and u.fragment is None
    assert str(u) == "http://example.com/index.html"

    # empty path is preserved
    u = parse("http://example.com")
    assert u.path == ""
    assert str(u) == "http://example.com"
    u = parse("http://example.com?a=1")
    assert u.path == "" and u.query == [("a", "1")]

    # -- relative (no scheme/host) URLs --
    u = parse("/docs/guide.html?v=2#s3")
    assert u.scheme is None and u.host is None and u.port is None
    assert u.path == "/docs/guide.html"
    assert u.query == [("v", "2")] and u.fragment == "s3"
    assert str(u) == "/docs/guide.html?v=2#s3"

    # -- query details --
    u = parse("http://h/p?flag&x=1&x=2")
    assert u.query == [("flag", ""), ("x", "1"), ("x", "2")]
    assert u.get("x") == "1"
    assert u.get_all("x") == ["1", "2"]
    assert u.get("missing") is None
    # a key with an empty value renders as the bare key
    assert str(u) == "http://h/p?flag&x=1&x=2"
    # keys are percent-decoded as well
    u = parse("/p?a%20b=1")
    assert u.query == [("a b", "1")]
    # empty pair segments are ignored
    u = parse("/p?a=1&&b=2")
    assert u.query == [("a", "1"), ("b", "2")]
    # bare "?" normalizes away
    assert str(parse("http://h/p?")) == "http://h/p"
    # empty fragment is kept
    assert parse("http://h/p#").fragment == ""
    assert str(parse("http://h/p#")) == "http://h/p#"

    # -- query editing preserves order --
    u = parse("/search?b=2&a=1&b=3")
    u.replace("b", "9")                      # first occurrence keeps its slot
    assert u.query == [("b", "9"), ("a", "1")]
    removed = u.remove("a")
    assert removed == 1
    assert u.query == [("b", "9")]
    u.add("c", "x")
    u.add("b", "10")
    assert u.query == [("b", "9"), ("c", "x"), ("b", "10")]
    assert u.remove("nope") == 0
    # replace of an absent key appends
    u2 = parse("/p?a=1")
    u2.replace("z", "5")
    assert u2.query == [("a", "1"), ("z", "5")]

    # -- query encoding on build --
    u = parse("/p")
    u.add("q", "café & tea")
    assert str(u) == "/p?q=caf%C3%A9+%26+tea"
    u = parse("/p")
    u.add("sum", "1+1=2")
    assert str(u) == "/p?sum=1%2B1%3D2"
    assert parse(str(u)).query == [("sum", "1+1=2")]   # round-trips

    # -- path normalization --
    assert normalize_path("/a/b/../c") == "/a/c"
    assert normalize_path("/a/./b/./c") == "/a/b/c"
    assert normalize_path("/../a") == "/a"             # clamped at the root
    assert normalize_path("/a/b/c/../../d") == "/a/d"
    assert normalize_path("a/b/../../../c") == "../c"  # relative paths keep ..
    assert normalize_path("../../x") == "../../x"
    assert normalize_path("/a/b/..") == "/a/"
    assert normalize_path("/a/b/.") == "/a/b/"
    assert normalize_path("/") == "/"
    assert normalize_path("") == ""
    assert normalize_path("./a") == "a"
    assert normalize_path("/a//b") == "/a//b"          # empty segments untouched

    u = parse("http://example.com/app/../static/./logo.png?v=1")
    result = u.normalize()
    assert result is u                                  # chainable
    assert str(u) == "http://example.com/static/logo.png?v=1"

    # -- errors --
    expect_value_error(parse, "http://example.com:port/x")   # non-numeric port
    expect_value_error(parse, "http://example.com:/x")       # empty port
    expect_value_error(parse, "http://example.com:99999/x")  # port out of range
    expect_value_error(parse, "http://user:pw@example.com/") # userinfo unsupported
    expect_value_error(parse, "/p?a=%GZ")                    # bad percent escape
    expect_value_error(parse, "/p?a=%2")                     # truncated escape

    print("ok")


if __name__ == "__main__":
    main()
