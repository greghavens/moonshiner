"""RSS 2.0 feed builder for the blog engine.

build_feed() turns a channel dict plus a list of item dicts into the XML
string we serve at /feed.xml.  The formatting is stable on purpose — the
golden files in the web repo diff against it byte for byte, so indentation
and element order never change between releases.
"""
from xml.sax.saxutils import escape

CHANNEL_REQUIRED = ("title", "link", "description")


def _tag(name, text, indent):
    """One ``<name>text</name>`` line with escaped text content."""
    return "%s<%s>%s</%s>" % (" " * indent, name, escape(str(text)), name)


def render_item(item):
    """Render one ``<item>`` block as a list of lines.

    ``title`` is mandatory; ``link``, ``description`` and ``pubdate`` are
    emitted only when present, always in that order.
    """
    if not item.get("title"):
        raise ValueError("item missing title")
    lines = ["    <item>"]
    lines.append(_tag("title", item["title"], 6))
    if item.get("link"):
        lines.append(_tag("link", item["link"], 6))
    if item.get("description"):
        lines.append(_tag("description", item["description"], 6))
    if item.get("pubdate"):
        lines.append(_tag("pubDate", item["pubdate"], 6))
    lines.append("    </item>")
    return lines


def build_feed(channel, items):
    """Build the full RSS document and return it as one string.

    The channel dict must provide title, link and description; items are
    rendered in the order given.
    """
    for field in CHANNEL_REQUIRED:
        if not channel.get(field):
            raise ValueError("channel missing %s" % field)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "  <channel>",
    ]
    for field in CHANNEL_REQUIRED:
        lines.append(_tag(field, channel[field], 4))
    for item in items:
        lines.extend(render_item(item))
    lines.append("  </channel>")
    lines.append("</rss>")
    return "\n".join(lines)
