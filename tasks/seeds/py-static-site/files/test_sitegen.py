"""Acceptance tests for sitegen. Run: python3 test_sitegen.py"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}

HELLO = """---
title: Hello, World & Friends
date: 2026-01-05
---
# Welcome

This is the **first** post on my `new` site.
It spans two lines.

## Details

- fast builds
- zero deps
- 1 < 2 & 3 > 2

Inline code keeps angles: `a<b>`.
"""

ALPHA = """---
title: Alpha Release
date: 2026-02-01
---
# Alpha

Ship *fast*, ship **often**.
"""

BETA = """---
title: Beta Notes
date: 2026-02-01
---
Beta is coming along.
"""

DRAFT = """---
title: Secret Plans
date: 2026-03-01
draft: true
---
Nobody should see this.
"""


def build(src, out):
    return subprocess.run([sys.executable, "sitegen.py", "build", src, out],
                          capture_output=True, text=True, env=ENV, timeout=30)


def write_sources(src):
    os.makedirs(src)
    for name, text in [("hello.md", HELLO), ("alpha.md", ALPHA),
                       ("beta.md", BETA), ("zulu-draft.md", DRAFT)]:
        with open(os.path.join(src, name), "w") as f:
            f.write(text)
    with open(os.path.join(src, "notes.txt"), "w") as f:
        f.write("not markdown, ignore me\n")


def read(path):
    with open(path) as f:
        return f.read()


def in_order(haystack, *needles):
    pos = -1
    for n in needles:
        nxt = haystack.find(n, pos + 1)
        assert nxt > pos, f"missing or out of order: {n!r}\n--- in ---\n{haystack}"
        pos = nxt


def main():
    tmp = tempfile.mkdtemp(dir=".")
    src = os.path.join(tmp, "src")
    out = os.path.join(tmp, "out")
    write_sources(src)
    try:
        r = build(src, out)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)

        # pages for every non-draft source, index, nothing else
        assert sorted(os.listdir(out)) == ["alpha.html", "beta.html", "hello.html",
                                           "index.html"], os.listdir(out)

        hello = read(os.path.join(out, "hello.html"))
        # title is escaped
        assert "<title>Hello, World &amp; Friends</title>" in hello, hello
        # block conversion, in source order
        in_order(hello,
                 "<h1>Welcome</h1>",
                 "<p>This is the <strong>first</strong> post on my <code>new</code>"
                 " site. It spans two lines.</p>",
                 "<h2>Details</h2>",
                 "<li>fast builds</li>",
                 "<li>zero deps</li>",
                 "<li>1 &lt; 2 &amp; 3 &gt; 2</li>",
                 "<p>Inline code keeps angles: <code>a&lt;b&gt;</code>.</p>")
        assert "<ul>" in hello and "</ul>" in hello, hello
        # raw special characters must never leak through unescaped
        assert "1 < 2" not in hello and "a<b>" not in hello, hello

        alpha = read(os.path.join(out, "alpha.html"))
        assert "<title>Alpha Release</title>" in alpha, alpha
        assert "<p>Ship <em>fast</em>, ship <strong>often</strong>.</p>" in alpha, alpha

        # drafts and non-markdown files produce no pages
        assert not os.path.exists(os.path.join(out, "zulu-draft.html"))
        assert not os.path.exists(os.path.join(out, "notes.html"))

        # index: newest first, date ties by title, escaped link text, no drafts
        index = read(os.path.join(out, "index.html"))
        assert "<title>Index</title>" in index, index
        links = re.findall(r'<a href="([^"]+)">([^<]+)</a>', index)
        assert links == [
            ("alpha.html", "Alpha Release"),
            ("beta.html", "Beta Notes"),
            ("hello.html", "Hello, World &amp; Friends"),
        ], links
        assert "Secret Plans" not in index, index

        # determinism: a second build produces byte-identical output
        out2 = os.path.join(tmp, "out2")
        r = build(src, out2)
        assert r.returncode == 0, (r.returncode, r.stderr)
        for name in ["alpha.html", "beta.html", "hello.html", "index.html"]:
            with open(os.path.join(out, name), "rb") as f1, \
                 open(os.path.join(out2, name), "rb") as f2:
                assert f1.read() == f2.read(), f"non-deterministic output: {name}"

        # ---- all-or-nothing validation
        badsrc = os.path.join(tmp, "badsrc")
        os.makedirs(badsrc)
        with open(os.path.join(badsrc, "good.md"), "w") as f:
            f.write("---\ntitle: Fine\ndate: 2026-01-01\n---\nok\n")
        with open(os.path.join(badsrc, "bad.md"), "w") as f:
            f.write("---\ndate: 2026-01-02\n---\nno title here\n")
        badout = os.path.join(tmp, "badout")
        r = build(badsrc, badout)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "bad.md" in r.stderr, r.stderr
        leftover = os.listdir(badout) if os.path.isdir(badout) else []
        assert leftover == [], f"failed build must write nothing, found {leftover}"

        # malformed date is fatal too, and also names the file
        badsrc2 = os.path.join(tmp, "badsrc2")
        os.makedirs(badsrc2)
        with open(os.path.join(badsrc2, "when.md"), "w") as f:
            f.write("---\ntitle: When\ndate: February 1st\n---\nbody\n")
        badout2 = os.path.join(tmp, "badout2")
        r = build(badsrc2, badout2)
        assert r.returncode == 1 and "when.md" in r.stderr, (r.returncode, r.stderr)
        leftover = os.listdir(badout2) if os.path.isdir(badout2) else []
        assert leftover == [], leftover
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all sitegen checks passed")


if __name__ == "__main__":
    main()
