"""Acceptance tests for the slug generator and registry. Run: python3 test_slugger.py"""


def main():
    from slugger import slugify, SlugRegistry

    # -- the basics --
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("Top 10 Tips (2026)") == "top-10-tips-2026"
    assert slugify("  --Crazy___Spacing--  ") == "crazy-spacing"
    assert slugify("already-a-slug") == "already-a-slug"
    assert slugify("A") == "a"

    # -- separators collapse, edges are clean --
    assert slugify("a  b\t\nc") == "a-b-c"
    assert slugify("...dots...and...more...") == "dots-and-more"
    assert not slugify("x!y").startswith("-")
    assert slugify("100%") == "100"

    # -- unicode: accents fold to ascii --
    assert slugify("Café au Lait") == "cafe-au-lait"
    assert slugify("naïve résumé") == "naive-resume"
    assert slugify("smörgåsbord") == "smorgasbord"
    assert slugify("Nürnberg Süd") == "nurnberg-sud"

    # -- special letters with no unicode decomposition --
    assert slugify("straße") == "strasse"
    assert slugify("Ærø sø") == "aero-so"
    assert slugify("œuvre d'æther") == "oeuvre-d-aether"

    # -- what can't survive, drops --
    assert slugify("Tokyo 東京") == "tokyo"
    assert slugify("party 🎉 time") == "party-time"
    assert slugify("東京") == ""
    assert slugify("!!!") == ""
    assert slugify("") == ""

    # -- max_length prefers a word boundary, never a trailing hyphen --
    assert slugify("the quick brown fox", max_length=12) == "the-quick", \
        slugify("the quick brown fox", max_length=12)
    assert slugify("the quick brown fox", max_length=15) == "the-quick-brown"
    assert slugify("the quick brown fox", max_length=100) == "the-quick-brown-fox"
    assert slugify("supercalifragilistic", max_length=8) == "supercal", \
        "a single long word gets a hard cut"
    assert slugify("ab cd", max_length=3) == "ab"
    assert slugify("abcdef", max_length=6) == "abcdef"
    for bad in (0, -3):
        try:
            slugify("x", max_length=bad)
            assert False, "max_length < 1 should raise ValueError"
        except ValueError:
            pass

    # -- registry: first come, first served; then numbered from -2 --
    reg = SlugRegistry()
    assert reg.assign("My Report") == "my-report"
    assert reg.assign("My Report") == "my-report-2"
    assert reg.assign("My REPORT!") == "my-report-3"

    # -- a title that already looks like a numbered slug is its own base --
    assert reg.assign("My Report 2") == "my-report-2-2", \
        "colliding with an existing suffixed slug must still be resolved"

    # -- and the original base keeps counting where it left off --
    assert reg.assign("My Report") == "my-report-4"

    # -- reserved words are never handed out bare --
    reg = SlugRegistry(reserved=("admin", "api", "new"))
    assert reg.assign("Admin") == "admin-2"
    assert reg.assign("admin") == "admin-3"
    assert reg.assign("API") == "api-2"
    assert reg.assign("api docs") == "api-docs", \
        "reserved matches the whole slug, not a prefix"
    assert reg.assign("New") == "new-2"

    # -- unslugifiable titles fall back to 'untitled' --
    reg = SlugRegistry()
    assert reg.assign("!!!") == "untitled"
    assert reg.assign("東京") == "untitled-2"
    assert reg.assign("Untitled") == "untitled-3"

    # -- 'untitled' is a fallback, not a reservation --
    reg = SlugRegistry()
    assert reg.assign("Untitled") == "untitled"
    assert reg.assign("???") == "untitled-2"

    # -- registries are independent --
    a, b = SlugRegistry(), SlugRegistry()
    assert a.assign("post") == "post"
    assert b.assign("post") == "post"

    print("all slugger checks passed")


if __name__ == "__main__":
    main()
