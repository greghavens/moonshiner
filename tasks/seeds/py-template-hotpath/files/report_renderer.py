"""A tiny dependency-free renderer for line-oriented batch reports.

Templates use ``{{field_name}}`` placeholders. A ``ReportTemplate`` owns an
immutable source string and can be shared by any number of renderers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re


Token = tuple[str, str]
_PLACEHOLDER = re.compile(r"{{([A-Za-z_][A-Za-z0-9_]*)}}")


def parse_template(source: str) -> tuple[Token, ...]:
    """Compile template text into literal and field tokens."""
    tokens: list[Token] = []
    cursor = 0
    for match in _PLACEHOLDER.finditer(source):
        if match.start() > cursor:
            tokens.append(("literal", source[cursor : match.start()]))
        tokens.append(("field", match.group(1)))
        cursor = match.end()
    if cursor < len(source):
        tokens.append(("literal", source[cursor:]))
    return tuple(tokens)


@dataclass(frozen=True)
class ReportTemplate:
    """Immutable report-template source reusable across render operations."""

    source: str

    def render_row(self, record: Mapping[str, object]) -> str:
        pieces: list[str] = []
        for kind, value in parse_template(self.source):
            if kind == "literal":
                pieces.append(value)
            else:
                pieces.append(str(record[value]))
        return "".join(pieces)


class ReportRenderer:
    """Render records in input order with a shared report template."""

    def render(
        self,
        template: ReportTemplate,
        records: Iterable[Mapping[str, object]],
    ) -> str:
        return "".join(template.render_row(record) for record in records)
