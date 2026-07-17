"""Paging for the CLI's long outputs (`ourctl logs`, `ourctl diff`...).

Text is hard-wrapped to the terminal width, then chunked into pages of
`height` display lines. render_page() adds the footer the interactive
pager prints between keypresses.
"""


def wrap_line(line, width):
    """Hard-wrap one logical line into segments of at most `width` columns.

    An empty line stays a single empty segment so blank lines survive.
    """
    if width < 1:
        raise ValueError("width must be >= 1")
    if line == "":
        return [""]
    return [line[i:i + width] for i in range(0, len(line), width)]


def paginate(text, width, height):
    """Split `text` into pages: lists of at most `height` wrapped lines."""
    if height < 1:
        raise ValueError("height must be >= 1")
    rows = []
    for line in text.split("\n"):
        rows.extend(wrap_line(line, width))
    pages = [rows[i:i + height] for i in range(0, len(rows), height)]
    return pages or [[]]


def page_count(text, width, height):
    return len(paginate(text, width, height))


def render_page(pages, index):
    """One page as a printable block with the pager footer."""
    if not 0 <= index < len(pages):
        raise IndexError("page %d of %d" % (index, len(pages)))
    body = "\n".join(pages[index])
    footer = "-- page %d/%d --" % (index + 1, len(pages))
    return body + "\n" + footer
