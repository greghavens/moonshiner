"""JSONL is newline-delimited and nothing else.

str.splitlines breaks on every Unicode line boundary — U+2028, U+2029, \x0b,
\x85 — all of which appear legitimately inside JSON string values. Reading
JSONL that way tears objects in half and the parse fails on valid data. An
imported corpus of 2201 rows split into 2273 fragments and the publish queue
crash-looped 203 times on the resulting JSONDecodeError.
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from common import jsonl_lines  # noqa: E402

# Every character str.splitlines treats as a line boundary but JSON does not.
BOUNDARIES = [" ", " ", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85"]


class JsonlLines(unittest.TestCase):
    def _write(self, rows):
        handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False,
                                             encoding="utf-8")
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.close()
        return pathlib.Path(handle.name)

    def test_a_line_separator_inside_a_value_does_not_split_the_row(self):
        rows = [{"task": "a", "text": f"before{sep}after"} for sep in BOUNDARIES]
        path = self._write(rows)
        try:
            lines = jsonl_lines(path)
            self.assertEqual(len(lines), len(rows),
                             "one row per line regardless of embedded separators")
            for line in lines:
                json.loads(line)      # must parse; splitlines would tear these
        finally:
            path.unlink()

    def test_splitlines_would_have_broken_the_same_data(self):
        """Pins why the helper exists rather than reading the file directly."""
        rows = [{"task": "a", "text": "before after"}]
        path = self._write(rows)
        try:
            self.assertGreater(len(path.read_text().splitlines()), len(jsonl_lines(path)))
        finally:
            path.unlink()

    def test_blank_lines_are_dropped_and_content_preserved(self):
        path = self._write([{"task": "a"}, {"task": "b"}])
        try:
            path.write_text(path.read_text() + "\n\n")
            self.assertEqual([json.loads(l)["task"] for l in jsonl_lines(path)],
                             ["a", "b"])
        finally:
            path.unlink()

    def test_undecodable_bytes_can_be_replaced(self):
        handle = tempfile.NamedTemporaryFile("wb", suffix=".jsonl", delete=False)
        handle.write(b'{"task": "a"}\n\xff\xfe bad bytes\n{"task": "b"}\n')
        handle.close()
        path = pathlib.Path(handle.name)
        try:
            self.assertEqual(len(jsonl_lines(path, errors="replace")), 3)
        finally:
            path.unlink()


class EveryJsonlReaderUsesIt(unittest.TestCase):
    def test_no_module_splits_a_jsonl_file_with_splitlines(self):
        offenders = []
        for path in sorted((ROOT / "src").rglob("*.py")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if ".splitlines()" not in line:
                    continue
                # SHA256SUMS and id lists are plain text, not JSONL.
                if any(token in line for token in ("sums", "ids_file", "SHA256")):
                    continue
                if "jsonl" in line.lower() or "json.loads" in line or "rows" in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [], "JSONL must be split on newlines only")


if __name__ == "__main__":
    unittest.main()
