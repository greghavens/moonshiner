"""Fixed-width tables for the ops report emails.

A Table is a declared column list plus rows; render() produces the
aligned plain-text block we drop into the nightly report body.
"""


class Table:
    def __init__(self, columns):
        cols = list(columns)
        if not cols:
            raise ValueError("at least one column required")
        if len(set(cols)) != len(cols):
            raise ValueError("duplicate column name")
        if any(not str(c).strip() for c in cols):
            raise ValueError("blank column name")
        self.columns = cols
        self._rows = []

    def add_row(self, values):
        """Add one row from a mapping; missing columns become ''. """
        row = dict(values)
        unknown = set(row) - set(self.columns)
        if unknown:
            raise ValueError("unknown column(s): %s" % sorted(unknown))
        self._rows.append({c: row.get(c, "") for c in self.columns})

    def row_count(self):
        return len(self._rows)

    def rows(self):
        """Row dicts in insertion order (copies — callers can't mutate us)."""
        return [dict(r) for r in self._rows]

    def render(self):
        """Aligned text: header, dashed rule, one line per row."""
        widths = []
        for c in self.columns:
            cell_widths = [len(str(r[c])) for r in self._rows]
            widths.append(max([len(str(c))] + cell_widths))

        def line(cells):
            padded = [str(v).ljust(w) for v, w in zip(cells, widths)]
            return "  ".join(padded).rstrip()

        out = [line(self.columns)]
        out.append(line(["-" * w for w in widths]))
        for r in self._rows:
            out.append(line([r[c] for c in self.columns]))
        return "\n".join(out)
