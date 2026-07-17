"""Acceptance tests for staticfiles.StaticFiles — the gateway's static-file core.

Pure in-process contract tests: no sockets, no threads. The suite builds its
own docroot under the current directory (srvtree/), pins every mtime with
os.utime so validator headers are deterministic, and drives
StaticFiles.handle() directly with raw request-targets.

Run: python3 test_staticfiles.py
"""
import os
import shutil
from email.utils import formatdate

from staticfiles import StaticFiles

BASE = os.path.join(os.getcwd(), "srvtree")
DOCROOT = os.path.join(BASE, "docroot")
FIXED_MTIME = 1714565400  # Wed, 01 May 2024 12:10:00 GMT
NEW_MTIME = 1720000000    # Wed, 03 Jul 2024 09:46:40 GMT (file-changed test)

HELLO = b"Hello, static world!\n"  # 21 bytes
CSS = b"body { margin: 0; }\n"
PNG = b"\x89PNG\r\n\x1a\nnot-a-real-png"
HTML = b"<!doctype html><h1>hi</h1>\n"
JSON_DOC = b'{"ok": true}\n'
FIRMWARE = b"\x00\x01\x02\xfe\xff\r\n\x89PNG"
NOTES = b"v2.1: fixed the flux capacitor\n"
MUTABLE_V1 = b"cache me v1\n"
MUTABLE_V2 = b"cache me v2, longer now\n"
SECRET = b"TOP SECRET: never serve this\n"

TREE = {
    "hello.txt": HELLO,
    "index.html": HTML,
    "data.json": JSON_DOC,
    "firmware.bin": FIRMWARE,
    "empty.bin": b"",
    "release notes.txt": NOTES,
    "mutable.txt": MUTABLE_V1,
    os.path.join("assets", "site.css"): CSS,
    os.path.join("assets", "logo.png"): PNG,
}


def setup_tree():
    if os.path.exists(BASE):
        shutil.rmtree(BASE)
    for rel, content in TREE.items():
        path = os.path.join(DOCROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))
    outside = os.path.join(BASE, "outside.txt")
    with open(outside, "wb") as f:
        f.write(SECRET)
    os.utime(outside, (FIXED_MTIME, FIXED_MTIME))


def handler():
    return StaticFiles(DOCROOT)


def h(resp, name):
    """Case-insensitive response-header lookup; None when absent."""
    for key, value in resp.headers.items():
        if str(key).lower() == name.lower():
            return value
    return None


def get(target, headers=None, method="GET"):
    resp = handler().handle(method, target, headers or {})
    assert isinstance(resp.status, int), f"status must be int, got {type(resp.status)}"
    assert isinstance(resp.body, (bytes, bytearray)), (
        f"body must be bytes, got {type(resp.body)} for {target!r}"
    )
    return resp


def body_len_matches(resp, context):
    cl = h(resp, "Content-Length")
    assert cl is not None, f"{context}: Content-Length missing"
    assert int(cl) == len(resp.body), (
        f"{context}: Content-Length {cl} but body is {len(resp.body)} bytes"
    )


def test_plain_get():
    resp = get("/hello.txt")
    assert resp.status == 200, resp.status
    assert bytes(resp.body) == HELLO, resp.body
    assert int(h(resp, "Content-Length")) == 21, h(resp, "Content-Length")
    assert h(resp, "Content-Type") == "text/plain", h(resp, "Content-Type")
    assert h(resp, "Accept-Ranges") == "bytes", h(resp, "Accept-Ranges")
    etag = h(resp, "ETag")
    assert etag is not None, "200 must carry an ETag"
    assert etag.startswith('"') and etag.endswith('"') and len(etag) > 2, (
        f"ETag must be a non-empty quoted string, got {etag!r}"
    )
    assert h(resp, "Last-Modified") == formatdate(FIXED_MTIME, usegmt=True), (
        h(resp, "Last-Modified")
    )
    again = get("/hello.txt")
    assert h(again, "ETag") == etag, "ETag must be stable while the file is unchanged"


def test_content_types_and_bytes():
    cases = [
        ("/index.html", "text/html", HTML),
        ("/data.json", "application/json", JSON_DOC),
        ("/assets/site.css", "text/css", CSS),
        ("/assets/logo.png", "image/png", PNG),
        ("/firmware.bin", "application/octet-stream", FIRMWARE),
        ("/release%20notes.txt", "text/plain", NOTES),
    ]
    for target, ctype, content in cases:
        resp = get(target)
        assert resp.status == 200, f"{target}: {resp.status}"
        assert h(resp, "Content-Type") == ctype, (
            f"{target}: expected {ctype}, got {h(resp, 'Content-Type')}"
        )
        assert bytes(resp.body) == content, f"{target}: body altered in transit"
        body_len_matches(resp, target)


def test_empty_file():
    resp = get("/empty.bin")
    assert resp.status == 200, resp.status
    assert bytes(resp.body) == b"", resp.body
    assert int(h(resp, "Content-Length")) == 0, h(resp, "Content-Length")
    assert h(resp, "Content-Type") == "application/octet-stream"
    assert h(resp, "Accept-Ranges") == "bytes"
    assert h(resp, "ETag") is not None, "empty files still get validators"


def test_head_mirrors_get():
    full = get("/hello.txt")
    resp = get("/hello.txt", method="HEAD")
    assert resp.status == 200, resp.status
    assert bytes(resp.body) == b"", "HEAD must not carry a body"
    assert int(h(resp, "Content-Length")) == 21, (
        "HEAD Content-Length must be the GET body size"
    )
    assert h(resp, "ETag") == h(full, "ETag"), "HEAD/GET validators must agree"
    assert h(resp, "Last-Modified") == h(full, "Last-Modified")
    assert h(resp, "Accept-Ranges") == "bytes"

    missing = get("/nope.txt", method="HEAD")
    assert missing.status == 404, missing.status

    ranged = get("/hello.txt", {"Range": "bytes=0-4"}, method="HEAD")
    assert ranged.status == 200, "Range must be ignored on HEAD (RFC 7233)"
    assert int(h(ranged, "Content-Length")) == 21, h(ranged, "Content-Length")
    assert bytes(ranged.body) == b""


def test_405_other_methods():
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        resp = get("/hello.txt", method=method)
        assert resp.status == 405, f"{method}: {resp.status}"
        assert h(resp, "Allow") == "GET, HEAD", f"{method}: Allow={h(resp, 'Allow')!r}"
        assert h(resp, "Content-Type") == "text/plain", h(resp, "Content-Type")
        body_len_matches(resp, f"405 for {method}")


def test_404_missing_and_directories():
    for target in ("/nope.txt", "/assets", "/assets/", "/", "/hello.txt/extra",
                   "/deep/deeper/nope.png"):
        resp = get(target)
        assert resp.status == 404, f"{target}: expected 404, got {resp.status}"
        assert h(resp, "Content-Type") == "text/plain", h(resp, "Content-Type")
        body_len_matches(resp, f"404 for {target}")


def test_dot_segments_query_fragment():
    resp = get("/./hello.txt")
    assert resp.status == 200 and bytes(resp.body) == HELLO, (
        "single-dot segments must be dropped, not rejected"
    )
    for target in ("/hello.txt?v=9", "/hello.txt#frag", "/hello.txt?v=1#frag"):
        resp = get(target)
        assert resp.status == 200, f"{target}: {resp.status}"
        assert bytes(resp.body) == HELLO, f"{target}: query/fragment must be stripped"
    resp = get("/assets/site.css?x=1&y=2")
    assert resp.status == 200 and bytes(resp.body) == CSS, resp.status


def test_traversal_rejected():
    assert os.path.exists(os.path.join(BASE, "outside.txt")), "fixture sanity"
    attempts = [
        "/../outside.txt",
        "/%2e%2e/outside.txt",
        "/%2E%2E/outside.txt",
        "/assets/../../outside.txt",
        "/assets/%2e%2e/%2e%2e/outside.txt",
        "/..%2foutside.txt",
        "/%2e%2e%2foutside.txt",
        "/..\\outside.txt",
        "/assets\\..\\..\\outside.txt",
        "/hello.txt%00.png",
        "//outside.txt",
        "/assets/../hello.txt",  # dot-dot is rejected even when it stays inside
    ]
    for target in attempts:
        resp = get(target)
        assert resp.status == 404, (
            f"{target!r}: expected plain 404, got {resp.status}"
        )
        assert SECRET not in bytes(resp.body), f"{target!r}: leaked file outside docroot"


def test_percent_decode_happens_once():
    resp = get("/%252e%252e/outside.txt")  # decodes to literal '%2e%2e' segment
    assert resp.status == 404, resp.status
    assert SECRET not in bytes(resp.body), "double-decoded a traversal target"


def test_single_range_variants():
    cases = [
        ("bytes=0-4", b"Hello", "bytes 0-4/21"),
        ("bytes=7-12", b"static", "bytes 7-12/21"),
        ("bytes=14-", b"world!\n", "bytes 14-20/21"),
        ("bytes=-6", b"orld!\n", "bytes 15-20/21"),
        ("bytes=0-0", b"H", "bytes 0-0/21"),
        ("bytes=18-9999", b"d!\n", "bytes 18-20/21"),
        ("bytes=-999", HELLO, "bytes 0-20/21"),
    ]
    for spec, expected, content_range in cases:
        resp = get("/hello.txt", {"Range": spec})
        assert resp.status == 206, f"{spec}: expected 206, got {resp.status}"
        assert bytes(resp.body) == expected, f"{spec}: body {resp.body!r}"
        assert h(resp, "Content-Range") == content_range, (
            f"{spec}: Content-Range {h(resp, 'Content-Range')!r}"
        )
        assert int(h(resp, "Content-Length")) == len(expected), (
            f"{spec}: Content-Length must match the slice"
        )
        assert h(resp, "ETag") is not None, f"{spec}: 206 keeps validators"
        assert h(resp, "Last-Modified") is not None, f"{spec}: 206 keeps validators"


def test_range_unsatisfiable():
    for spec in ("bytes=21-", "bytes=100-200", "bytes=-0"):
        resp = get("/hello.txt", {"Range": spec})
        assert resp.status == 416, f"{spec}: expected 416, got {resp.status}"
        assert h(resp, "Content-Range") == "bytes */21", (
            f"{spec}: Content-Range {h(resp, 'Content-Range')!r}"
        )
        body_len_matches(resp, f"416 for {spec}")
    resp = get("/empty.bin", {"Range": "bytes=0-"})
    assert resp.status == 416, f"any range on an empty file is 416, got {resp.status}"
    assert h(resp, "Content-Range") == "bytes */0", h(resp, "Content-Range")


def test_bad_ranges_fall_back_to_200():
    for spec in ("bytes=5-2", "bytes=abc", "chars=0-4", "bytes=0-1,3-5",
                 "bytes=", "bytes=-"):
        resp = get("/hello.txt", {"Range": spec})
        assert resp.status == 200, f"{spec!r}: expected full 200, got {resp.status}"
        assert bytes(resp.body) == HELLO, f"{spec!r}: body {resp.body!r}"
        assert int(h(resp, "Content-Length")) == 21, h(resp, "Content-Length")
        assert h(resp, "Content-Range") is None, (
            f"{spec!r}: no Content-Range on a full response"
        )


def assert_304(resp, etag, context):
    assert resp.status == 304, f"{context}: expected 304, got {resp.status}"
    assert bytes(resp.body) == b"", f"{context}: 304 must have no body"
    assert h(resp, "ETag") == etag, f"{context}: 304 must echo the ETag"
    assert h(resp, "Content-Length") is None, f"{context}: no Content-Length on 304"
    assert h(resp, "Content-Range") is None, f"{context}: no Content-Range on 304"


def test_if_none_match():
    etag = h(get("/hello.txt"), "ETag")
    assert_304(get("/hello.txt", {"If-None-Match": etag}), etag, "exact match")
    assert_304(get("/hello.txt", {"If-None-Match": f'"stale-a", {etag}, "stale-b"'}),
               etag, "match inside a list")
    assert_304(get("/hello.txt", {"If-None-Match": "*"}), etag, "star form")
    assert_304(get("/hello.txt", {"If-None-Match": f"W/{etag}"}), etag,
               "weak comparison: W/-prefixed tag matches its strong form")
    fresh = get("/hello.txt", {"If-None-Match": '"deadbeef"'})
    assert fresh.status == 200 and bytes(fresh.body) == HELLO, (
        "non-matching If-None-Match serves the file"
    )
    missing = get("/nope.txt", {"If-None-Match": "*"})
    assert missing.status == 404, "conditionals never resurrect a missing file"


def test_if_modified_since():
    same = get("/hello.txt", {"If-Modified-Since": formatdate(FIXED_MTIME, usegmt=True)})
    etag = h(get("/hello.txt"), "ETag")
    assert_304(same, etag, "IMS equal to mtime")
    later = get("/hello.txt",
                {"If-Modified-Since": formatdate(FIXED_MTIME + 3600, usegmt=True)})
    assert_304(later, etag, "IMS after mtime")
    earlier = get("/hello.txt",
                  {"If-Modified-Since": formatdate(FIXED_MTIME - 3600, usegmt=True)})
    assert earlier.status == 200, f"stale IMS must serve the file, got {earlier.status}"
    for bad in ("not a date", ""):
        resp = get("/hello.txt", {"If-Modified-Since": bad})
        assert resp.status == 200, (
            f"unparseable IMS {bad!r} must be ignored, got {resp.status}"
        )
        assert bytes(resp.body) == HELLO


def test_inm_takes_precedence_over_ims():
    etag = h(get("/hello.txt"), "ETag")
    resp = get("/hello.txt", {
        "If-None-Match": '"something-else"',
        "If-Modified-Since": formatdate(FIXED_MTIME, usegmt=True),
    })
    assert resp.status == 200, (
        "when If-None-Match is present and fails, If-Modified-Since is ignored "
        f"(RFC 7232) — got {resp.status}"
    )
    assert bytes(resp.body) == HELLO
    resp = get("/hello.txt", {
        "If-None-Match": etag,
        "If-Modified-Since": formatdate(FIXED_MTIME - 86400, usegmt=True),
    })
    assert_304(resp, etag, "matching INM wins even with a stale IMS")


def test_conditional_beats_range():
    etag = h(get("/hello.txt"), "ETag")
    resp = get("/hello.txt", {"If-None-Match": etag, "Range": "bytes=0-4"})
    assert_304(resp, etag, "304 wins over Range")
    resp = get("/hello.txt", {"If-None-Match": '"other"', "Range": "bytes=0-4"})
    assert resp.status == 206 and bytes(resp.body) == b"Hello", (
        f"failed conditional proceeds to the range: {resp.status} {resp.body!r}"
    )


def test_etag_tracks_file_changes():
    first = get("/mutable.txt")
    etag1 = h(first, "ETag")
    assert bytes(first.body) == MUTABLE_V1
    path = os.path.join(DOCROOT, "mutable.txt")
    with open(path, "wb") as handle:
        handle.write(MUTABLE_V2)
    os.utime(path, (NEW_MTIME, NEW_MTIME))
    second = get("/mutable.txt")
    assert second.status == 200 and bytes(second.body) == MUTABLE_V2
    etag2 = h(second, "ETag")
    assert etag2 != etag1, "ETag must change when the file changes"
    assert h(second, "Last-Modified") == formatdate(NEW_MTIME, usegmt=True), (
        h(second, "Last-Modified")
    )
    revalidate = get("/mutable.txt", {"If-None-Match": etag1})
    assert revalidate.status == 200 and bytes(revalidate.body) == MUTABLE_V2, (
        "a stale validator must get the new content, not a 304"
    )
    assert_304(get("/mutable.txt", {"If-None-Match": etag2}), etag2,
               "fresh validator after change")


def test_distinct_files_distinct_etags():
    assert h(get("/hello.txt"), "ETag") != h(get("/data.json"), "ETag"), (
        "different files must not share an ETag"
    )


def main():
    tests = [
        test_plain_get,
        test_content_types_and_bytes,
        test_empty_file,
        test_head_mirrors_get,
        test_405_other_methods,
        test_404_missing_and_directories,
        test_dot_segments_query_fragment,
        test_traversal_rejected,
        test_percent_decode_happens_once,
        test_single_range_variants,
        test_range_unsatisfiable,
        test_bad_ranges_fall_back_to_200,
        test_if_none_match,
        test_if_modified_since,
        test_inm_takes_precedence_over_ims,
        test_conditional_beats_range,
        test_etag_tracks_file_changes,
        test_distinct_files_distinct_etags,
    ]
    setup_tree()
    try:
        for test in tests:
            test()
            print(f"ok - {test.__name__}")
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
