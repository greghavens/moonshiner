"""Streaming CSV ingest for supplier stock feeds.

Feeds arrive as arbitrary-size byte chunks from the transfer layer; chunk
boundaries carry no meaning and may split characters, line endings, or a
leading UTF-8 BOM. Records use standard quoting: fields may contain commas,
escaped quotes ("") and line breaks; embedded line breaks are normalized to
"\n". A BOM is consumed only at the very start of the stream — U+FEFF
anywhere later is field data. Blank lines are ignored but still counted.
Rows and problems are reported with the physical line number where the
record starts (the header is line 1).
"""

import codecs
import csv


class FeedReader:
    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._tail = ""
        self._record = None
        self._record_line = 0
        self._line = 0
        self._at_start = True
        self.header = None
        self.rows = []
        self.problems = []

    def feed(self, chunk):
        text = self._decoder.decode(chunk)
        if self._at_start:
            if text.startswith("\ufeff"):
                text = text[1:]
            self._at_start = False
        self._tail += text
        pieces = self._tail.split("\n")
        self._tail = pieces.pop()
        for piece in pieces:
            self._line += 1
            self._take_line(piece[:-1] if piece.endswith("\r") else piece)

    def _take_line(self, line):
        if self._record is None:
            if line == "":
                return
            self._record = line
            self._record_line = self._line
        else:
            self._record += line
        if self._record.count('"') % 2 == 0:
            self._emit(self._record, self._record_line)
            self._record = None

    def _emit(self, text, line):
        fields = next(csv.reader([text]))
        if self.header is None:
            self.header = fields
            return
        if len(fields) != len(self.header):
            self.problems.append(
                {
                    "line": line,
                    "reason": f"expected {len(self.header)} fields, got {len(fields)}",
                }
            )
            return
        self.rows.append((line, dict(zip(self.header, fields))))

    def finish(self):
        self._decoder.decode(b"", True)
        if self._tail:
            self._line += 1
            self._take_line(self._tail)
            self._tail = ""
        return {"header": self.header, "rows": self.rows, "problems": self.problems}


def ingest(chunks):
    reader = FeedReader()
    for chunk in chunks:
        reader.feed(chunk)
    return reader.finish()
