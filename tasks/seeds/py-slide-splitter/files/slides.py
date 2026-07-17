"""Split a markdown talk deck into slides for the previewer.

Authors write one markdown file per talk; slides are separated by a line
containing exactly ``---``.  split_slides() carves the document up and pulls
a title out of each slide's first heading so the preview sidebar has
something to show.
"""

DELIMITER = "---"


def _chunks(text):
    """Yield the raw line lists between delimiter lines."""
    current = []
    for line in text.splitlines():
        if line == DELIMITER:
            yield current
            current = []
        else:
            current.append(line)
    yield current


def _trim(lines):
    """Drop leading and trailing blank lines, in place."""
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def _extract_title(lines):
    """Pull the slide title from its first markdown heading.

    A heading is 1-6 '#' characters followed by a space; the first one wins
    and its line is removed from the slide.  Returns (title, remaining) —
    title is None when the slide has no heading.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if 1 <= hashes <= 6 and stripped[hashes:].startswith(" "):
            return stripped[hashes:].strip(), lines[:i] + lines[i + 1:]
    return None, lines


def split_slides(text):
    """Split a markdown document into slide dicts.

    Each slide has a ``title`` (from its first heading, or None) and a
    ``body`` (everything else, trimmed of surrounding blank lines).  Slides
    that end up completely empty are dropped.
    """
    slides = []
    for raw in _chunks(text):
        lines = _trim(list(raw))
        if not lines:
            continue
        title, rest = _extract_title(lines)
        body = "\n".join(_trim(rest))
        slides.append({"title": title, "body": body})
    return slides
