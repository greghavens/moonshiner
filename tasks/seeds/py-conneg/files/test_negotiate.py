"""Acceptance tests for negotiate.py -- content-negotiation engine.

Run: python3 test_negotiate.py
"""
from negotiate import choose, negotiate, parse_accept


def triples(items):
    return [(i["type"], i["subtype"], i["q"]) for i in items]


def test_parse_accept_precedence_order():
    items = parse_accept("text/*;q=0.5, text/html, application/json;q=0.9, */*;q=0.1")
    assert triples(items) == [
        ("text", "html", 1.0),
        ("application", "json", 0.9),
        ("text", "*", 0.5),
        ("*", "*", 0.1),
    ], triples(items)

    # same q: exact beats type/* beats */*; same specificity keeps header order
    items = parse_accept("*/*, text/*, text/plain, text/html")
    assert triples(items) == [
        ("text", "plain", 1.0),
        ("text", "html", 1.0),
        ("text", "*", 1.0),
        ("*", "*", 1.0),
    ], triples(items)


def test_parse_accept_params_and_normalization():
    items = parse_accept(" Application/JSON ; version=2 ; q=0.8 , text/html;level=1 ")
    assert triples(items) == [("text", "html", 1.0), ("application", "json", 0.8)], triples(items)
    html, json_item = items
    assert html["params"] == {"level": "1"}, html
    assert json_item["params"] == {"version": "2"}, json_item
    assert "q" not in html["params"] and "q" not in json_item["params"]


def test_parse_accept_skips_malformed_items():
    items = parse_accept("garbage, /nosubtype, text/;q=0.5, application/json;q=oops, text/html;q=0.8")
    assert triples(items) == [("text", "html", 0.8)], triples(items)
    assert parse_accept("%%%") == []
    assert parse_accept("") == []


def test_negotiate_absent_accept_means_first_available():
    available = ["application/json", "text/html"]
    assert negotiate(None, available) == "application/json"
    assert negotiate("", available) == "application/json"
    assert negotiate("   ", available) == "application/json"


def test_negotiate_q_ordering():
    available = ["text/html", "application/json"]
    assert negotiate("text/html;q=0.4, application/json;q=0.9", available) == "application/json"
    assert negotiate("text/html;q=0.9, application/json;q=0.4", available) == "text/html"
    assert negotiate("application/json;q=0.001", available) == "application/json"


def test_negotiate_most_specific_match_governs_q():
    # the broad */* does NOT rescue a type that was explicitly demoted
    available = ["application/json", "text/html"]
    got = negotiate("*/*;q=1, application/json;q=0.5", available)
    assert got == "text/html", got
    # and type/* is more specific than */*
    got = negotiate("*/*;q=0.9, image/*;q=0.2", ["image/png", "text/plain"])
    assert got == "text/plain", got


def test_negotiate_q_zero_excludes():
    assert negotiate("*/*, text/plain;q=0", ["text/plain"]) is None
    assert negotiate("*/*, text/plain;q=0", ["text/plain", "text/html"]) == "text/html"


def test_negotiate_ties_go_to_server_preference():
    assert negotiate("*/*", ["text/html", "application/json"]) == "text/html"
    assert negotiate("application/json, text/html", ["text/html", "application/json"]) == "text/html"
    assert negotiate("image/*", ["image/png", "image/webp"]) == "image/png"


def test_negotiate_case_insensitive_returns_available_spelling():
    assert negotiate("TEXT/HTML", ["text/html"]) == "text/html"
    assert negotiate("text/html", ["Text/HTML"]) == "Text/HTML"
    assert negotiate("Image/*;Q=0.5", ["image/png"]) == "image/png"


def test_negotiate_wildcard_subtype_matching():
    assert negotiate("image/*", ["image/png", "text/html"]) == "image/png"
    assert negotiate("image/*", ["text/html"]) is None
    assert negotiate("audio/mpeg", ["text/html", "application/json"]) is None


def test_negotiate_all_items_malformed_is_not_acceptable():
    assert negotiate("garbage", ["text/html"]) is None
    assert negotiate("text/;q=1, /html", ["text/html"]) is None


def test_choose_strict_and_default_policies():
    available = ["application/json", "text/html"]
    assert choose("text/html", available) == (200, "text/html", "Accept")
    assert choose("application/xml", available, policy="strict") == (406, None, "Accept")
    assert choose("application/xml", available, policy="default") == (200, "application/json", "Accept")
    assert choose(None, available) == (200, "application/json", "Accept")


def test_choose_vary_only_when_there_is_a_choice():
    assert choose("text/html", ["text/html"]) == (200, "text/html", None)
    assert choose("application/xml", ["text/html"], policy="strict") == (406, None, None)
    assert choose(None, ["text/html"]) == (200, "text/html", None)


def test_choose_rejects_unknown_policy():
    try:
        choose("text/html", ["text/html"], policy="lenient")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown policy must raise ValueError")


def main():
    tests = [
        test_parse_accept_precedence_order,
        test_parse_accept_params_and_normalization,
        test_parse_accept_skips_malformed_items,
        test_negotiate_absent_accept_means_first_available,
        test_negotiate_q_ordering,
        test_negotiate_most_specific_match_governs_q,
        test_negotiate_q_zero_excludes,
        test_negotiate_ties_go_to_server_preference,
        test_negotiate_case_insensitive_returns_available_spelling,
        test_negotiate_wildcard_subtype_matching,
        test_negotiate_all_items_malformed_is_not_acceptable,
        test_choose_strict_and_default_policies,
        test_choose_vary_only_when_there_is_a_choice,
        test_choose_rejects_unknown_policy,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
