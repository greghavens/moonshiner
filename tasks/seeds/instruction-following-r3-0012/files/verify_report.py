#!/usr/bin/env python3
"""Deterministic acceptance checks for the final policy brief."""

from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlparse


REPORT = Path("policy_brief.md")
TITLE = "# Consumer-Facing Automated Decision Duties in Colorado, Connecticut, and Texas"
SECTIONS = [
    "## Executive summary",
    "## Scope and legal status",
    "## Comparison",
    "## State analysis",
    "## Cross-state takeaways",
    "## Sources",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def word_count(markdown: str) -> int:
    without_urls = re.sub(r"\]\(https?://[^)]+\)", "]", markdown)
    return len(re.findall(r"\b[\w][\w’'/-]*\b", without_urls, flags=re.UNICODE))


def markdown_links(markdown: str) -> list[tuple[str, str]]:
    return re.findall(r"\[([^\]]+)\]\((https?://[^)]+)\)", markdown)


def host_is(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def require_comparison_table(comparison: str) -> None:
    lines = comparison.splitlines()
    tables: list[tuple[list[str], list[str]]] = []
    for index in range(len(lines) - 1):
        header = table_cells(lines[index])
        divider = table_cells(lines[index + 1])
        if len(header) < 4 or len(header) != len(divider):
            continue
        if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in divider):
            continue
        rows = []
        for line in lines[index + 2:]:
            if "|" not in line:
                break
            rows.append(line)
        tables.append((header, rows))

    require(bool(tables), "Comparison must contain a Markdown table")
    for header, rows in tables:
        labels = " | ".join(header).lower()
        table_text = labels + "\n" + "\n".join(rows).lower()
        header_coverage = (
            re.search(r"\b(jurisdiction|state)\b", labels)
            and re.search(r"\b(authority|law|statute|governing|legal basis)\b", labels)
            and re.search(r"\b(status|effective|timing|posture)\b", labels)
            and re.search(r"\b(pre[- ]?decision|before|at use|notice)\b", labels)
            and re.search(r"\b(post[- ]?decision|after|result|outcome|disclosure|explanation)\b", labels)
            and re.search(r"\b(review|appeal|reconsideration|recourse|challenge)\b", labels)
            and re.search(r"\b(pre[- ]?decision|before|prior|at use)\b", table_text)
            and re.search(r"\b(post[- ]?decision|after|result|outcome|adverse)\b", table_text)
        )
        row_text = "\n".join(rows)
        state_coverage = all(
            re.search(rf"\b{state}\b", row_text)
            for state in ("Colorado", "Connecticut", "Texas")
        )
        if header_coverage and state_coverage:
            return
    raise AssertionError(
        "comparison table must cover jurisdiction, authority/status, "
        "pre- and post-decision duties, and review/appeal for all three states"
    )


def require_sources(body: str, sources: str) -> int:
    source_blocks = []
    start = 0
    for match in re.finditer(r"Accessed August 15, 2026\.?", sources):
        block = sources[start:match.end()].strip()
        if block:
            source_blocks.append(block)
        start = match.end()
    require(len(source_blocks) >= 6, "Sources needs at least six dated entries")

    entries: list[tuple[str, str, str]] = []
    for block in source_blocks:
        links = markdown_links(block)
        require(bool(links), "each dated source entry must contain a Markdown link")
        for label, url in links:
            parsed = urlparse(url)
            require(parsed.scheme in {"http", "https"} and bool(parsed.netloc),
                    "source links must be direct HTTP(S) links")
            query_keys = {key.lower() for key in parse_qs(parsed.query)}
            require("search" not in parsed.path.lower()
                    and not query_keys.intersection({"q", "query", "search"}),
                    "source links must not be search-result URLs")
            entries.append((block, label, url))

    canonical_urls = {
        url.split("#", 1)[0].rstrip("/").lower() for _, _, url in entries
    }
    require(len(canonical_urls) >= 6, "Sources needs at least six distinct links")

    official_domains = {
        "Colorado": ("leg.colorado.gov", "coag.gov", "colorado.gov"),
        "Connecticut": ("cga.ct.gov", "portal.ct.gov", "ct.gov"),
        "Texas": (
            "statutes.capitol.texas.gov",
            "capitol.texas.gov",
            "tcss.legis.texas.gov",
            "legis.state.tx.us",
            "texasattorneygeneral.gov",
            "texas.gov",
        ),
    }
    issuer_patterns = {
        "Colorado": (
            r"\bColorado (?:General Assembly|Department|Division|Office|Attorney General|"
            r"Governor|Secretary|Commission|Board|Judicial Branch)\b"
        ),
        "Connecticut": (
            r"\bConnecticut (?:General Assembly|Department|Division|Office|Attorney General|"
            r"Governor|Secretary|Commission|Board|Judicial Branch)\b"
        ),
        "Texas": (
            r"\b(?:Texas (?:Legislature(?: Online)?|Department|Division|Office|Attorney General|"
            r"Governor|Secretary|Commission|Board|Judicial Branch)|"
            r"Office of (?:the )?Texas Attorney General)\b"
        ),
    }

    state_entries: dict[str, list[tuple[str, str, str]]] = {}
    for state, domains in official_domains.items():
        matching = [
            entry for entry in entries
            if host_is((urlparse(entry[2]).hostname or "").lower(), domains)
        ]
        unique = {entry[2].split("#", 1)[0].rstrip("/").lower() for entry in matching}
        require(len(unique) >= 2, f"Sources needs two distinct official {state} links")
        require(all(re.search(issuer_patterns[state], entry[0], flags=re.IGNORECASE)
                    for entry in matching),
                f"each {state} source must identify its issuing body")
        state_entries[state] = matching

    enacted_patterns = {
        "Colorado": r"leg\.colorado\.gov/(?:laws/session-laws|bill_files)/",
        "Connecticut": r"cga\.ct\.gov/(?:\d{4}/(?:act|sup)/|current/pub/)",
        "Texas": (
            r"(?:statutes\.capitol\.texas\.gov/Docs/|tcss\.legis\.texas\.gov/resources/|"
            r"(?:capitol\.texas\.gov|legis\.state\.tx\.us)/tlodocs/\d+R/billtext/"
            r"(?:html|pdf)/[A-Z]{2}\d+F\.)"
        ),
    }
    for state, pattern in enacted_patterns.items():
        require(any(re.search(pattern, url, flags=re.IGNORECASE)
                    for _, _, url in state_entries[state]),
                f"Sources needs enacted or codified text for {state}")

    body_links = markdown_links(body)
    require(len(body_links) >= 3, "factual discussion needs inline Markdown links")
    for state, domains in official_domains.items():
        require(any(host_is((urlparse(url).hostname or "").lower(), domains)
                    for _, url in body_links),
                f"factual discussion needs inline official support for {state}")

    for _, url in markdown_links(body + "\n" + sources):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        query_keys = {key.lower() for key in parse_qs(parsed.query)}
        require(host.endswith(".gov") or host_is(host, ("state.co.us", "state.tx.us")),
                "all citations must link to official government pages or documents")
        require("search" not in parsed.path.lower()
                and not query_keys.intersection({"q", "query", "search"}),
                "citations must not use search-result URLs")

    return len(source_blocks)


def main() -> None:
    require(REPORT.is_file(), "policy_brief.md is missing")
    text = REPORT.read_text(encoding="utf-8")
    require(text.startswith(TITLE + "\n"), "revised exact title is missing")
    require(len(re.findall(rf"^{re.escape(TITLE)}$", text, flags=re.MULTILINE)) == 1,
            "title must appear exactly once")

    positions = []
    for heading in SECTIONS:
        matches = list(re.finditer(rf"^{re.escape(heading)}$", text, flags=re.MULTILINE))
        require(len(matches) == 1, f"missing or duplicate section: {heading}")
        positions.append(matches[0].start())
    require(positions == sorted(positions), "required sections are out of order")
    require(re.search(r"Accessed August 15, 2026\.?\s*$", text) is not None,
            "Sources must be the final section")

    body, sources = text.split("## Sources", 1)
    count = word_count(body)
    require(1000 <= count <= 1200, f"body word count is {count}, expected 1000–1200")

    state_analysis = body[body.index("## State analysis"):body.index("## Cross-state takeaways")]
    state_headings = []
    for state in ("Colorado", "Connecticut", "Texas"):
        heading = f"### {state}"
        matches = list(re.finditer(rf"^{re.escape(heading)}$", state_analysis,
                                   flags=re.MULTILINE))
        require(len(matches) == 1, f"missing or duplicate state heading: {state}")
        state_headings.append(matches[0].start())
    require(state_headings == sorted(state_headings), "state analyses are out of order")
    require(not re.search(r"\bVirginia\b", text, flags=re.IGNORECASE),
            "superseded Virginia scope remains in the brief")

    comparison = body[body.index("## Comparison"):body.index("## State analysis")]
    require_comparison_table(comparison)

    lower = body.lower()
    fact_patterns = {
        "as-of date": (r"august 15, 2026",),
        "Colorado current authority": (
            r"(?:s\.?b\.?|senate bill)\s*24[-‐‑‒–— ]205",
            r"june 30, 2026",
        ),
        "Colorado replacement and status": (
            r"(?:s\.?b\.?|senate bill)\s*26[-‐‑‒–— ]189",
            r"january 1, 2027",
            r"(?:stay|bar|pause|prevent|prohibit|enjoin|suspend)[a-z -]{0,30}enforce",
        ),
        "Colorado consumer process": (
            r"(?:30|thirty)[ -]days?",
            r"(?:reason|explanation)",
            r"appeal",
            r"human review",
            r"reconsider(?:ation|ed)",
        ),
        "Connecticut authority": (r"42[-‐‑‒–—]518", r"july 1, 2026"),
        "Connecticut profiling rights": (
            r"(?:question|challenge|ask(?: questions?)?)[a-z ]{0,20}(?:result|outcome|decision)",
            r"(?:reason|explanation)",
            r"profiling",
        ),
        "Texas privacy authority": (r"(?:chapter|ch\.?)\s*541", r"july 1, 2024"),
        "Texas request process": (
            r"opt[- ]out",
            r"appeal",
            r"(?:60|sixty)[ -]days?",
        ),
        "enforcement context": (r"attorney general",),
    }
    for label, patterns in fact_patterns.items():
        require(all(re.search(pattern, lower) for pattern in patterns),
                f"brief does not substantively cover {label}")

    exclusion_patterns = {
        "generally applicable private-entity scope": (
            r"(?:generally applicable|general application).{0,50}(?:private|business)"
        ),
        "government-use exclusion": (
            r"(?:government|public[- ]sector|state agenc).{0,50}(?:exclude|outside|not covered)"
            r"|(?:exclude|outside).{0,50}(?:government|public[- ]sector|state agenc)"
        ),
        "local-ordinance exclusion": r"local ordinances?|municipal (?:rules|ordinances)",
        "unenacted-bill exclusion": r"unenacted bills?|pending (?:bills|proposals)",
        "single-sector exclusion": r"sector[- ]specific|industry[- ]specific|single sector",
    }
    for label, pattern in exclusion_patterns.items():
        require(re.search(pattern, lower), f"brief does not explain {label}")

    require(not re.search(
        r"^#{2,4} .*\b(risk assessments?|bias testing|governance programs?|prohibited uses?)\b",
        body, flags=re.IGNORECASE | re.MULTILINE),
        "superseded broad obligations may not receive dedicated coverage")
    require(not re.search(
        r"^#{1,4}\s+(?:policy\s+)?recommendations?\s*$|"
        r"\b(?:legislators?|lawmakers?|states?) should\b",
        body, flags=re.IGNORECASE | re.MULTILINE),
            "policy recommendations are outside scope")

    source_count = require_sources(body, sources)

    stripped_links = re.sub(r"\[[^\]]+\]\(https?://[^)]+\)", "", text)
    require(not re.search(r"https?://", stripped_links),
            "URLs must be Markdown links, not raw URLs")
    require(not re.search(r"\[\^[^\]]+\]|<sup\b", text, flags=re.IGNORECASE),
            "footnotes are not allowed")
    quoted = re.findall(r'[“\"]([^“”\"\n]+)[”\"]', body)
    require(all(word_count(item) <= 20 for item in quoted),
            "a quotation exceeds 20 consecutive words")
    block_quotes = re.findall(r"(?:^>.*(?:\n|$))+", body, flags=re.MULTILINE)
    require(all(word_count(item) <= 20 for item in block_quotes),
            "a block quotation exceeds 20 consecutive words")

    print(f"policy brief accepted ({count} body words, {source_count} dated source entries)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
