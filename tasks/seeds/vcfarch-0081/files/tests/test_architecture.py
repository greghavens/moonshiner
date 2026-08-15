"""Protected acceptance test entry point."""

from datetime import date
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit

from vcf_arch.verify import verify_file


ROOT = Path(__file__).resolve().parent.parent

SOURCE_ENTRY = re.compile(
    r"(?m)^-\s+(?P<title>\S[^\n]*?)\s*\n"
    r"\s+URL:\s+(?P<url>https://\S+)\s*\n"
    r"\s+Accessed:\s+(?P<accessed>\d{4}-\d{2}-\d{2})\s*\n"
    r"\s+Used for:\s+(?P<claim>\S[^\n]*)\s*$"
)


def verify_research(path: Path) -> None:
    if not path.is_file():
        raise AssertionError(f"missing required research record: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AssertionError(f"cannot read research record: {exc}") from exc

    entries = list(SOURCE_ENTRY.finditer(text))
    if not entries:
        raise AssertionError("research.md has no correctly formatted source entries")

    label_counts = {
        label: len(re.findall(rf"(?m)^\s+{re.escape(label)}:\s+\S", text))
        for label in ("URL", "Accessed", "Used for")
    }
    if any(count != len(entries) for count in label_counts.values()):
        raise AssertionError("every research source must have one URL, Accessed, and Used for field")

    urls: list[str] = []
    searchable: list[str] = []
    for entry in entries:
        title = entry.group("title").strip(" *_`")
        claim = entry.group("claim").strip()
        if not title or not claim:
            raise AssertionError("research source titles and claims must be nonempty")

        url = entry.group("url")
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        official = any(
            hostname == domain or hostname.endswith(f".{domain}")
            for domain in ("broadcom.com", "vmware.com")
        )
        if parsed.scheme != "https" or not official:
            raise AssertionError(f"research URL is not an official Broadcom/VMware HTTPS source: {url}")
        urls.append(url)

        try:
            date.fromisoformat(entry.group("accessed"))
        except ValueError as exc:
            raise AssertionError(f"invalid research access date: {entry.group('accessed')}") from exc
        searchable.extend((title.lower(), claim.lower()))

    if len(urls) != len(set(urls)):
        raise AssertionError("research.md repeats a source URL")

    research_claims = " ".join(searchable)
    required_topics = {
        "compatibility/interoperability": ("compatib", "interop"),
        "upgrade path or sequence": ("upgrade",),
        "component release/BOM": ("bill of materials", "bom", "component build", "component version"),
    }
    for label, keywords in required_topics.items():
        if not any(keyword in research_claims for keyword in keywords):
            raise AssertionError(f"research.md does not document {label} material")


class ArchitectureAcceptanceTest(unittest.TestCase):
    def test_architecture(self) -> None:
        verify_file(ROOT / "architecture.json")

    def test_research_record(self) -> None:
        verify_research(ROOT / "research.md")


if __name__ == "__main__":
    unittest.main()
