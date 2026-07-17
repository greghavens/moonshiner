"""Acceptance checks for rssfeed.py. Run: python3 test_rssfeed.py"""
import rssfeed
from rssfeed import build_feed

CHANNEL = {
    "title": "Ship Log",
    "link": "https://example.com/",
    "description": "Release notes",
}


# ---------------------------------------------------------------- existing

def test_minimal_feed_renders_exact_xml():
    items = [
        {"title": "v1.2", "link": "https://example.com/v12",
         "description": "Bug fixes",
         "pubdate": "Fri, 21 Nov 1997 09:55:06 -0600"},
        {"title": "v1.1"},
    ]
    expected = "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
        "    <title>Ship Log</title>",
        "    <link>https://example.com/</link>",
        "    <description>Release notes</description>",
        "    <item>",
        "      <title>v1.2</title>",
        "      <link>https://example.com/v12</link>",
        "      <description>Bug fixes</description>",
        "      <pubDate>Fri, 21 Nov 1997 09:55:06 -0600</pubDate>",
        "    </item>",
        "    <item>",
        "      <title>v1.1</title>",
        "    </item>",
        "  </channel>",
        "</rss>",
    ])
    assert build_feed(CHANNEL, items) == expected


def test_reserved_characters_are_escaped():
    channel = dict(CHANNEL, description="News & <notes>")
    out = build_feed(channel, [{"title": "Tom & Jerry <3"}])
    assert "    <description>News &amp; &lt;notes&gt;</description>" in out
    assert "      <title>Tom &amp; Jerry &lt;3</title>" in out
    assert "<3" not in out.replace("&lt;3", "")


def test_missing_channel_fields_raise():
    for field in ("title", "link", "description"):
        broken = dict(CHANNEL)
        del broken[field]
        try:
            build_feed(broken, [])
            assert False, "accepted channel without %s" % field
        except ValueError:
            pass


def test_item_without_title_is_rejected():
    try:
        build_feed(CHANNEL, [{"description": "no title here"}])
        assert False, "accepted an item without a title"
    except ValueError:
        pass


def test_optional_item_fields_are_omitted():
    out = build_feed(CHANNEL, [{"title": "bare"}])
    lines = out.splitlines()
    start = lines.index("    <item>")
    assert lines[start:start + 3] == [
        "    <item>", "      <title>bare</title>", "    </item>"]


# ----------------------------------------------------------------- feature

def test_enclosure_renders_with_escaped_attributes():
    items = [{"title": "Ep 1", "enclosure": {
        "url": "https://cdn.example.com/ep1.mp3?a=1&b=2",
        "type": "audio/mpeg", "length": 123456}}]
    out = build_feed(CHANNEL, items)
    assert ('      <enclosure url="https://cdn.example.com/ep1.mp3?a=1&amp;b=2"'
            ' length="123456" type="audio/mpeg"/>') in out.splitlines()


def test_categories_render_in_order_after_description():
    channel = dict(CHANNEL, categories=["Dev", "R&D"])
    items = [{"title": "v1.2", "categories": ["news", "tools"]}]
    lines = build_feed(channel, items).splitlines()
    i = lines.index("    <description>Release notes</description>")
    assert lines[i + 1] == "    <category>Dev</category>"
    assert lines[i + 2] == "    <category>R&amp;D</category>"
    j = lines.index("      <title>v1.2</title>")
    assert lines[j + 1] == "      <category>news</category>"
    assert lines[j + 2] == "      <category>tools</category>"


def test_valid_feed_has_no_validation_errors():
    items = [{"title": "Ep 1",
              "pubdate": "Fri, 21 Nov 1997 09:55:06 -0600",
              "enclosure": {"url": "https://x/e.mp3", "type": "audio/mpeg",
                            "length": "2048"}}]
    assert rssfeed.validate_feed(CHANNEL, items) == []


def test_channel_errors_come_first_in_field_order():
    errors = rssfeed.validate_feed({}, [{"description": "untitled"}])
    assert errors == [
        "channel: missing title",
        "channel: missing link",
        "channel: missing description",
        "item 1: missing title",
    ], errors


def test_bad_pubdates_are_flagged_not_raised():
    bad = [{"title": "x", "pubdate": "yesterday"}]
    assert rssfeed.validate_feed(CHANNEL, bad) == [
        "item 1: invalid pubDate 'yesterday'"]
    good = [{"title": "x", "pubdate": "Sat, 01 Jun 2024 08:00:00 +0000"}]
    assert rssfeed.validate_feed(CHANNEL, good) == []


def test_enclosure_problems_are_collected_in_order():
    items = [{"title": "Ep 1",
              "enclosure": {"type": "mpeg", "length": "12kb"}}]
    assert rssfeed.validate_feed(CHANNEL, items) == [
        "item 1: enclosure missing url",
        "item 1: enclosure type 'mpeg' is not type/subtype",
        "item 1: enclosure length '12kb' is not a non-negative integer",
    ]


def test_enclosure_length_accepts_ints_and_digit_strings():
    def enc(length):
        return [{"title": "e", "enclosure": {
            "url": "https://x/e.mp3", "type": "audio/mpeg", "length": length}}]
    assert rssfeed.validate_feed(CHANNEL, enc(0)) == []
    assert rssfeed.validate_feed(CHANNEL, enc("2048")) == []
    assert rssfeed.validate_feed(CHANNEL, enc(-5)) == [
        "item 1: enclosure length -5 is not a non-negative integer"]


def test_items_are_numbered_from_one():
    items = [{"title": "ok"}, {"pubdate": "bogus"}, {}]
    assert rssfeed.validate_feed(CHANNEL, items) == [
        "item 2: missing title",
        "item 2: invalid pubDate 'bogus'",
        "item 3: missing title",
    ]


def test_build_feed_raises_with_all_errors_joined():
    channel = dict(CHANNEL)
    del channel["link"]
    try:
        build_feed(channel, [{"pubdate": "bogus", "title": "x"}])
        assert False, "build_feed accepted an invalid feed"
    except ValueError as e:
        assert str(e) == ("channel: missing link; "
                          "item 1: invalid pubDate 'bogus'"), str(e)


EXISTING = [
    test_minimal_feed_renders_exact_xml,
    test_reserved_characters_are_escaped,
    test_missing_channel_fields_raise,
    test_item_without_title_is_rejected,
    test_optional_item_fields_are_omitted,
]

FEATURE = [
    test_enclosure_renders_with_escaped_attributes,
    test_categories_render_in_order_after_description,
    test_valid_feed_has_no_validation_errors,
    test_channel_errors_come_first_in_field_order,
    test_bad_pubdates_are_flagged_not_raised,
    test_enclosure_problems_are_collected_in_order,
    test_enclosure_length_accepts_ints_and_digit_strings,
    test_items_are_numbered_from_one,
    test_build_feed_raises_with_all_errors_joined,
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
