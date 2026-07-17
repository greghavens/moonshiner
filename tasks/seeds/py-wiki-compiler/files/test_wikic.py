"""Acceptance tests for the wiki compiler. Run: python3 test_wikic.py"""
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def wikic(*args):
    return subprocess.run([sys.executable, "wikic.py", *args],
                          capture_output=True, text=True, env=ENV, timeout=30)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def build_fixture(src):
    write(os.path.join(src, "home.md"),
          "# Home\n\nStart with [[Getting Started]] and the"
          " [[glossary|Glossary of Terms]].\n\nSee also [[Deploy Guide]]"
          " and [[zz-intro|the intro]].\n")
    write(os.path.join(src, "getting-started.md"),
          "# Getting Started\n\nInstall the tool,\nthen skim the [[glossary]].\n\n"
          "Read the [[glossary]] again later.\n\nBack to [[Home]].\n")
    write(os.path.join(src, "glossary.md"),
          "# Glossary\n\nTerms & jargon used across the wiki.\n")
    write(os.path.join(src, "deploy-guide.md"),
          "# Deploy Guide\n\nShip according to the [[glossary]] and the"
          " [[Release Train]].\n")
    write(os.path.join(src, "scratch.md"),
          "# Scratch\n\nHalf-formed ideas, see [[scratch]] and [[Old Draft]].\n")
    write(os.path.join(src, "zz-intro.md"),
          "# About This Wiki\n\nWhy this exists.\n")
    write(os.path.join(src, "notes.txt"), "not a wiki page\n")


def main():
    tmp = tempfile.mkdtemp(dir=".")
    try:
        src = os.path.join(tmp, "src")
        out = os.path.join(tmp, "out")
        build_fixture(src)

        r = wikic("build", src, out)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)

        produced = sorted(os.listdir(out))
        assert produced == ["deploy-guide.html", "getting-started.html",
                            "glossary.html", "home.html", "index.html",
                            "scratch.html", "zz-intro.html"], produced

        home = read(os.path.join(out, "home.html"))
        assert "<h1>Home</h1>" in home, home
        # wikilinks resolve case-insensitively to <slug>.html
        assert '<a href="getting-started.html">Getting Started</a>' in home, home
        # piped links use the display text
        assert '<a href="glossary.html">Glossary of Terms</a>' in home, home
        assert '<a href="zz-intro.html">the intro</a>' in home, home
        assert '<a href="deploy-guide.html">Deploy Guide</a>' in home, home

        gs = read(os.path.join(out, "getting-started.html"))
        # a paragraph's source lines are joined with single spaces
        assert ('<p>Install the tool, then skim the'
                ' <a href="glossary.html">glossary</a>.</p>') in gs, gs
        assert '<a href="home.html">Home</a>' in gs, gs

        # page text is HTML-escaped
        gl = read(os.path.join(out, "glossary.html"))
        assert "<p>Terms &amp; jargon used across the wiki.</p>" in gl, gl

        # unresolvable links render as a marked span; build still succeeds
        dg = read(os.path.join(out, "deploy-guide.html"))
        assert '<span class="missing">Release Train</span>' in dg, dg

        # backlinks: glossary is linked from three pages, each listed once,
        # ordered by title
        assert "<h2>Backlinks</h2>" in gl, gl
        bl = gl.split("<h2>Backlinks</h2>", 1)[1]
        assert bl.count('href="getting-started.html"') == 1, \
            ("a page linking twice appears once", bl)
        i_dg = bl.find('href="deploy-guide.html"')
        i_gs = bl.find('href="getting-started.html"')
        i_home = bl.find('href="home.html"')
        assert -1 not in (i_dg, i_gs, i_home), bl
        assert i_dg < i_gs < i_home, ("backlinks sorted by title", bl)

        # a page nobody else links to has no backlinks section; self-links
        # don't count
        sc = read(os.path.join(out, "scratch.html"))
        assert "<h2>Backlinks</h2>" not in sc, sc

        # home's backlinks come only from getting-started
        home_bl = home.split("<h2>Backlinks</h2>", 1)[1]
        assert 'href="getting-started.html"' in home_bl, home_bl
        assert 'href="deploy-guide.html"' not in home_bl, home_bl

        # index.html lists every page sorted by TITLE (zz-intro's title starts
        # with 'About', so it must come first despite its slug)
        idx = read(os.path.join(out, "index.html"))
        order = [idx.find(f'href="{slug}.html"') for slug in
                 ["zz-intro", "deploy-guide", "getting-started",
                  "glossary", "home", "scratch"]]
        assert -1 not in order, idx
        assert order == sorted(order), ("index sorted by title", idx)
        assert ">About This Wiki</a>" in idx, idx
        assert ">Deploy Guide</a>" in idx, idx

        # non-.md sources are ignored
        assert "notes.txt" not in produced and "notes.html" not in produced, produced

        # builds are deterministic: byte-identical output on a second run
        out2 = os.path.join(tmp, "out2")
        r = wikic("build", src, out2)
        assert r.returncode == 0, r.stderr
        for name in produced:
            with open(os.path.join(out, name), "rb") as f1, \
                 open(os.path.join(out2, name), "rb") as f2:
                assert f1.read() == f2.read(), (name, "output must be deterministic")

        # check: broken links first, then orphans, both sorted; exit 1
        r = wikic("check", src)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.splitlines() == [
            "broken: deploy-guide -> Release Train",
            "broken: scratch -> Old Draft",
            "orphan: scratch",
        ], r.stdout

        # a clean wiki: home is exempt from orphan detection
        src2 = os.path.join(tmp, "src2")
        write(os.path.join(src2, "home.md"), "# Home\n\nGo read [[praxis]].\n")
        write(os.path.join(src2, "praxis.md"), "# Praxis\n\nLoops back to [[home]].\n")
        r = wikic("check", src2)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert r.stdout.strip() == "ok", r.stdout

        # a page without a '# Title' first line is a build error naming the file
        src3 = os.path.join(tmp, "src3")
        write(os.path.join(src3, "home.md"), "# Home\n\nfine\n")
        write(os.path.join(src3, "broken-page.md"), "no heading here\n")
        r = wikic("build", src3, os.path.join(tmp, "out3"))
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "broken-page" in r.stderr, r.stderr

        # missing source dir is a usage error
        r = wikic("build", os.path.join(tmp, "nowhere"), os.path.join(tmp, "out4"))
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert r.stderr.strip(), "expected an error message on stderr"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all wiki compiler checks passed")


if __name__ == "__main__":
    main()
