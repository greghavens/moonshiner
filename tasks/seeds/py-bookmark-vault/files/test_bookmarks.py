"""Acceptance tests for the bookmark vault. Run: python3 test_bookmarks.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def cli(db, *args):
    return subprocess.run(
        [sys.executable, "cli.py", "--db", db, *args],
        capture_output=True, text=True, env=ENV, timeout=30)


def ok(db, *args):
    p = cli(db, *args)
    assert p.returncode == 0, (args, p.returncode, p.stderr)
    return p


def search_lines(db, *args):
    return ok(db, "search", *args).stdout.splitlines()


def test_urlnorm():
    import urlnorm
    n = urlnorm.normalize
    assert n("HTTP://Example.COM:80/path/") == "http://example.com/path"
    assert n("https://example.com:443/") == "https://example.com"
    assert n("https://example.com/") == "https://example.com"
    assert n("https://example.com/a/b/") == "https://example.com/a/b"
    assert n("https://example.com/x#section-2") == "https://example.com/x"
    assert n("example.com/guide?b=2&a=1") == "https://example.com/guide?a=1&b=2"
    # stable sort: equal keys keep their relative order
    assert n("https://e.com/?b=2&a=1&a=0") == "https://e.com?a=1&a=0&b=2"
    assert n("https://example.com:8080/x") == "https://example.com:8080/x"
    # idempotent
    assert n(n("HTTP://Example.COM:80/path/?z=1&y=2#f")) == \
        n("HTTP://Example.COM:80/path/?z=1&y=2#f")


def test_netscape():
    import netscape
    books = [
        {"url": "https://b.example.com/x?a=1&b=2", "title": 'Tom & "Jerry" <dev>',
         "tags": ["tv", "fun"]},
        {"url": "https://a.example.com", "title": "Plain", "tags": []},
    ]
    text = netscape.render(books)
    assert text == (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        "<TITLE>Bookmarks</TITLE>\n"
        "<H1>Bookmarks</H1>\n"
        "<DL><p>\n"
        '    <DT><A HREF="https://a.example.com">Plain</A>\n'
        '    <DT><A HREF="https://b.example.com/x?a=1&amp;b=2" TAGS="fun,tv">'
        "Tom &amp; &quot;Jerry&quot; &lt;dev&gt;</A>\n"
        "</DL><p>\n"
    ), text
    back = netscape.parse(text)
    assert sorted(b["url"] for b in back) == \
        ["https://a.example.com", "https://b.example.com/x?a=1&b=2"]
    by_url = {b["url"]: b for b in back}
    assert by_url["https://b.example.com/x?a=1&b=2"]["title"] == 'Tom & "Jerry" <dev>'
    assert sorted(by_url["https://b.example.com/x?a=1&b=2"]["tags"]) == ["fun", "tv"]
    assert by_url["https://a.example.com"]["tags"] == []

    # survives real-browser junk: folders, odd whitespace
    messy = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        "<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n"
        "<DL><p>\n"
        "  <DT><H3 ADD_DATE=\"123\">Work stuff</H3>\n"
        "  <DL><p>\n"
        "        <DT><A HREF=\"https://wiki.example.com/Home\" TAGS=\"work\">Team wiki</A>\n"
        "  </DL><p>\n"
        "\t<DT><A HREF=\"https://news.example.com\">News &amp; views</A>\n"
        "</DL><p>\n"
    )
    got = netscape.parse(messy)
    assert len(got) == 2, got
    by_url = {b["url"]: b for b in got}
    assert by_url["https://wiki.example.com/Home"]["tags"] == ["work"]
    assert by_url["https://news.example.com"]["title"] == "News & views"


def test_cli(tmp):
    db = os.path.join(tmp, "vault.json")

    p = ok(db, "add", "HTTP://News.Ycombinator.COM:80/", "--title", "Hacker News",
           "--tag", "Tech", "--tag", "  daily   read ")
    assert p.stdout.strip() == "added http://news.ycombinator.com", p.stdout

    # same page through a different spelling merges; no --title keeps the old one
    p = ok(db, "add", "http://news.ycombinator.com/#top", "--tag", "news")
    assert p.stdout.strip() == "merged http://news.ycombinator.com", p.stdout

    # a brand-new URL without a title is an error
    p = cli(db, "add", "https://nowhere.example.com")
    assert p.returncode != 0

    ok(db, "add", "https://docs.python.org/3/library/", "--title", "Python stdlib docs",
       "--tag", "python", "--tag", "docs")
    ok(db, "add", "example.com/guide?b=2&a=1", "--title", "Setup Guide", "--tag", "docs")

    # search by term (case-insensitive, title or url), by tag, combined
    assert search_lines(db, "hacker") == [
        "http://news.ycombinator.com\tHacker News\tdaily-read,news,tech"]
    assert search_lines(db, "DOCS") == [
        "https://docs.python.org/3/library\tPython stdlib docs\tdocs,python"]
    assert search_lines(db, "--tag", "docs") == [
        "https://docs.python.org/3/library\tPython stdlib docs\tdocs,python",
        "https://example.com/guide?a=1&b=2\tSetup Guide\tdocs",
    ]
    assert search_lines(db, "--tag", "docs", "--tag", "python") == [
        "https://docs.python.org/3/library\tPython stdlib docs\tdocs,python"]
    assert search_lines(db, "zzz-nothing") == []

    # tag an existing bookmark through yet another URL spelling
    ok(db, "tag", "https://example.com/guide/?b=2&a=1#x", "Reference Material")
    assert search_lines(db, "guide") == [
        "https://example.com/guide?a=1&b=2\tSetup Guide\tdocs,reference-material"]
    p = cli(db, "tag", "https://unknown.example.com", "x")
    assert p.returncode != 0

    # legacy JSON import: urls normalized+deduped, tags kept verbatim
    legacy = os.path.join(tmp, "legacy.json")
    with open(legacy, "w") as f:
        json.dump([
            {"url": "https://blog.example.com/post/", "title": "Post",
             "tags": [" Reading ", "READING", "to read"]},
            {"url": "https://blog.example.com/post#comments", "title": "Post v2",
             "tags": ["reading"]},
        ], f)
    ok(db, "import", "--json", legacy)
    lines = search_lines(db, "blog.example.com")
    assert len(lines) == 1, lines
    url, title, tags = lines[0].split("\t")
    assert url == "https://blog.example.com/post"
    assert title == "Post v2"
    assert sorted(tags.split(",")) == sorted([" Reading ", "READING", "reading", "to read"])

    # repair normalizes the mess and reports how many bookmarks changed
    p = ok(db, "repair")
    assert p.stdout.strip() == "repaired 1 bookmarks", p.stdout
    assert search_lines(db, "blog.example.com") == [
        "https://blog.example.com/post\tPost v2\treading,to-read"]
    p = ok(db, "repair")
    assert p.stdout.strip() == "repaired 0 bookmarks", p.stdout

    # JSON export is sorted and clean
    out = os.path.join(tmp, "dump.json")
    ok(db, "export", "--json", out)
    with open(out) as f:
        dumped = json.load(f)
    assert [b["url"] for b in dumped] == sorted(b["url"] for b in dumped)
    by_url = {b["url"]: b for b in dumped}
    assert by_url["https://blog.example.com/post"]["tags"] == ["reading", "to-read"]

    # HTML round-trip into a fresh vault preserves everything
    html = os.path.join(tmp, "dump.html")
    ok(db, "export", "--html", html)
    db2 = os.path.join(tmp, "vault2.json")
    ok(db2, "import", "--html", html)
    assert search_lines(db2) == search_lines(db)


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    test_urlnorm()
    test_netscape()
    tmp = tempfile.mkdtemp(dir=".")
    try:
        test_cli(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK")


if __name__ == "__main__":
    main()
