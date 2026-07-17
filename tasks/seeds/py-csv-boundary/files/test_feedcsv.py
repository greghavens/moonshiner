"""Behavior contract for the chunked supplier-feed CSV reader."""

from feedcsv import ingest

SAMPLE_TEXT = (
    "sku,desc,qty\r\n"
    "A1,plain,4\r\n"
    'B2,"cold, brew",7\r\n'
    'C3,"two\r\nline note",9\r\n'
    'D4,"says ""hi""",2\r\n'
    "E5,café blend,3\r\n"
    "F6,too,many,fields\r\n"
    "G7,last,1\r\n"
    'H8,"\ufefftagged",5\r\n'
)
SAMPLE = b"\xef\xbb\xbf" + SAMPLE_TEXT.encode("utf-8")

EXPECTED_HEADER = ["sku", "desc", "qty"]
EXPECTED_ROWS = [
    (2, {"sku": "A1", "desc": "plain", "qty": "4"}),
    (3, {"sku": "B2", "desc": "cold, brew", "qty": "7"}),
    (4, {"sku": "C3", "desc": "two\nline note", "qty": "9"}),
    (6, {"sku": "D4", "desc": 'says "hi"', "qty": "2"}),
    (7, {"sku": "E5", "desc": "café blend", "qty": "3"}),
    (9, {"sku": "G7", "desc": "last", "qty": "1"}),
    (10, {"sku": "H8", "desc": "\ufefftagged", "qty": "5"}),
]
EXPECTED_PROBLEMS = [{"line": 8, "reason": "expected 3 fields, got 4"}]


def chunked(blob, size):
    return [blob[i : i + size] for i in range(0, len(blob), size)]


def check_report(report, label):
    assert report["header"] == EXPECTED_HEADER, f"{label}: header {report['header']!r}"
    assert report["rows"] == EXPECTED_ROWS, (
        f"{label}: rows differ\n got: {report['rows']!r}\nwant: {EXPECTED_ROWS!r}"
    )
    assert report["problems"] == EXPECTED_PROBLEMS, f"{label}: {report['problems']!r}"


def test_whole_blob():
    check_report(ingest([SAMPLE]), "whole blob")


def test_tiny_and_odd_chunk_sizes():
    for size in (1, 2, 3, 5, 8):
        check_report(ingest(chunked(SAMPLE, size)), f"chunk size {size}")


def test_empty_chunks_interleaved():
    parts = []
    for piece in chunked(SAMPLE, 3):
        parts.append(b"")
        parts.append(piece)
    parts.append(b"")
    check_report(ingest(parts), "empty chunks interleaved")


def test_stream_without_bom():
    report = ingest(chunked(SAMPLE_TEXT.encode("utf-8"), 2))
    check_report(report, "no BOM")


def test_lf_only_stream():
    text = 'sku,note\nA,plain\nB,"x\ny"\nC,end\n'
    report = ingest(chunked(text.encode("utf-8"), 1))
    assert report["header"] == ["sku", "note"]
    assert report["rows"] == [
        (2, {"sku": "A", "note": "plain"}),
        (3, {"sku": "B", "note": "x\ny"}),
        (5, {"sku": "C", "note": "end"}),
    ], report["rows"]
    assert report["problems"] == []


def test_blank_lines_ignored_but_counted():
    text = "sku,qty\n\nA,3\n\nB,4\n"
    report = ingest([text.encode("utf-8")])
    assert report["rows"] == [
        (3, {"sku": "A", "qty": "3"}),
        (5, {"sku": "B", "qty": "4"}),
    ]
    assert report["problems"] == []


def test_blank_line_inside_quoted_field():
    text = 'sku,note\nA,"top\n\nbottom"\nB,after\n'
    report = ingest(chunked(text.encode("utf-8"), 4))
    assert report["rows"] == [
        (2, {"sku": "A", "note": "top\n\nbottom"}),
        (5, {"sku": "B", "note": "after"}),
    ]
    assert report["problems"] == []


def test_unterminated_quote_reported_at_record_start():
    text = 'sku,qty\r\nA,1\r\nB,"open\r\n'
    report = ingest(chunked(text.encode("utf-8"), 2))
    assert report["rows"] == [(2, {"sku": "A", "qty": "1"})]
    assert report["problems"] == [
        {"line": 3, "reason": "unterminated quoted field"}
    ], "a record left open at end of stream must be reported, not dropped"


def test_final_record_without_trailing_newline():
    text = "sku,qty\nA,1\nB,2"
    report = ingest(chunked(text.encode("utf-8"), 3))
    assert report["rows"] == [
        (2, {"sku": "A", "qty": "1"}),
        (3, {"sku": "B", "qty": "2"}),
    ]
    assert report["problems"] == []


def main():
    tests = [fn for name, fn in sorted(list(globals().items())) if name.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"{len(tests)} checks passed")


if __name__ == "__main__":
    main()
