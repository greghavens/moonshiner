"""Behavior checks for the export batch. Run: python3 test_export_batch.py"""
from export_batch import Exporter


def render(name, source):
    """Tiny stand-in for the production renderer: {{ must be matched by }}."""
    if "{{" in source and "}}" not in source:
        raise ValueError(f"{name}: unclosed template tag")
    return f"<article>{source.strip()}</article>"


def test_clean_batch():
    docs = {
        "index.md": "Welcome",
        "about.md": "We make widgets",
    }
    report = Exporter(render, workers=2).run(docs)
    assert report.converted == {
        "index.md": "<article>Welcome</article>",
        "about.md": "<article>We make widgets</article>",
    }, report.converted
    assert report.failed == {}, report.failed
    assert report.accounted() == set(docs)
    assert report.summary() == "2 converted, 0 failed"


def test_batch_with_broken_documents():
    docs = {
        "index.md": "Welcome",
        "pricing.md": "Starts at {{ price",          # author forgot the }}
        "about.md": "We make widgets",
        "faq.md": "See {{ link",                      # same mistake, twice in one batch
        "contact.md": "mail us",
    }
    report = Exporter(render, workers=3).run(docs)

    assert report.accounted() == set(docs), (
        f"documents fell out of the report entirely: {set(docs) - report.accounted()}")
    assert sorted(report.converted) == ["about.md", "contact.md", "index.md"], (
        sorted(report.converted))
    assert sorted(report.failed) == ["faq.md", "pricing.md"], sorted(report.failed)
    for name in ("pricing.md", "faq.md"):
        assert "unclosed template tag" in report.failed[name], report.failed[name]
    assert report.summary() == "3 converted, 2 failed"


def main():
    test_clean_batch()
    test_batch_with_broken_documents()
    print("all checks passed")


if __name__ == "__main__":
    main()
