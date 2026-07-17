"""Replay tests for formdata.parse_form_data.

The multipart bodies below are reconstructions of real request payloads
captured from the intake service (tablet app, QA uploads, the label-printer
client), plus a few synthetic envelope checks. Byte-exact, no network.

Run: python3 test_formdata.py
"""
from formdata import MultipartError, parse_form_data


def crlf_body(*lines):
    return b"\r\n".join(lines) + b"\r\n"


DIGEST_PAYLOAD = (
    b"=== request 4402 ===\r\n"
    b"--oakBND31-inner\r\n"
    b"Content-Type: text/plain\r\n"
    b"\r\n"
    b"inner payload line\r\n"
    b"=== end ==="
)


def test_saved_digest_survives_intact():
    # QA uploads a saved message digest; the digest file is itself MIME text.
    body = crlf_body(
        b"--oakBND31",
        b'Content-Disposition: form-data; name="site"',
        b"",
        b"Pumphouse 7",
        b"--oakBND31",
        b'Content-Disposition: form-data; name="digest"; filename="qa-digest.eml"',
        b"Content-Type: message/rfc822",
        b"",
        b"=== request 4402 ===",
        b"--oakBND31-inner",
        b"Content-Type: text/plain",
        b"",
        b"inner payload line",
        b"=== end ===",
        b"--oakBND31",
        b'Content-Disposition: form-data; name="note"',
        b"",
        b"uploaded from tablet 6",
        b"--oakBND31--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=oakBND31")
    assert fd.fields == {"site": "Pumphouse 7", "note": "uploaded from tablet 6"}, (
        f"text fields wrong: {fd.fields!r}"
    )
    assert sorted(fd.files) == ["digest"], f"file parts wrong: {sorted(fd.files)}"
    digest = fd.files["digest"]
    assert digest.filename == "qa-digest.eml", digest.filename
    assert digest.content_type == "message/rfc822", digest.content_type
    assert digest.data == DIGEST_PAYLOAD, (
        "digest attachment corrupted: expected %d bytes, got %d: %r"
        % (len(DIGEST_PAYLOAD), len(digest.data), digest.data[:80])
    )
    assert digest.size == len(DIGEST_PAYLOAD), digest.size


ARCHIVE_PAYLOAD = (
    b"MIME-Version: 1.0\r\n"
    b'Content-Type: multipart/related; boundary="rk22swift--page"\r\n'
    b"\r\n"
    b"--rk22swift--page\r\n"
    b"<html>saved page one</html>\r\n"
    b"--rk22swift--page--"
)


def test_archive_upload_and_trailing_fields():
    # A saved .mht web archive followed by a plain text field.
    body = crlf_body(
        b"--rk22swift",
        b'Content-Disposition: form-data; name="report"; filename="section.mht"',
        b"Content-Type: application/x-mimearchive",
        b"",
        b"MIME-Version: 1.0",
        b'Content-Type: multipart/related; boundary="rk22swift--page"',
        b"",
        b"--rk22swift--page",
        b"<html>saved page one</html>",
        b"--rk22swift--page--",
        b"--rk22swift",
        b'Content-Disposition: form-data; name="email"',
        b"",
        b"ops@example.com",
        b"--rk22swift--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=rk22swift")
    assert "email" in fd.fields, (
        f"email field missing from the parse; fields = {sorted(fd.fields)}"
    )
    assert fd.fields["email"] == "ops@example.com", fd.fields["email"]
    assert "report" in fd.files, f"file parts wrong: {sorted(fd.files)}"
    report = fd.files["report"]
    assert report.filename == "section.mht", report.filename
    assert report.data == ARCHIVE_PAYLOAD, (
        "archive attachment corrupted: expected %d bytes, got %d: %r"
        % (len(ARCHIVE_PAYLOAD), len(report.data), report.data[:80])
    )


def test_accented_text_fields():
    body = crlf_body(
        b"--b7region",
        b'Content-Disposition: form-data; name="inspector"',
        b"",
        "Renée Müller-Åkesson".encode("utf-8"),
        b"--b7region",
        b'Content-Disposition: form-data; name="site"',
        b"",
        "Zürich – Halle 3".encode("utf-8"),
        b"--b7region--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=b7region")
    assert fd.fields["inspector"] == "Renée Müller-Åkesson", (
        f"inspector garbled: {fd.fields['inspector']!r}"
    )
    assert fd.fields["site"] == "Zürich – Halle 3", (
        f"site garbled: {fd.fields['site']!r}"
    )


def test_accented_filenames_and_binary_payload():
    payload = b"%PDF-1.7\n\xff\x00\xfe binary tail"
    body = crlf_body(
        b"--u9files",
        b'Content-Disposition: form-data; name="attachment"; filename="'
        + "rapport-préliminaire.pdf".encode("utf-8") + b'"',
        b"Content-Type: application/pdf",
        b"",
        payload,
        b"--u9files--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=u9files")
    att = fd.files["attachment"]
    assert att.filename == "rapport-préliminaire.pdf", (
        f"filename garbled: {att.filename!r}"
    )
    assert att.data == payload, "binary payload must pass through byte-for-byte"
    assert att.content_type == "application/pdf", att.content_type
    assert fd.fields == {}, fd.fields


def test_ascii_forms_roundtrip():
    body = crlf_body(
        b"--plainBnd1",
        b'Content-Disposition: form-data; name="title"',
        b"",
        b"weekly walkthrough",
        b"--plainBnd1",
        b'Content-Disposition: form-data; name="notes"',
        b"",
        b"first line",
        b"second line",
        b"--plainBnd1",
        b'Content-Disposition: form-data; name="log"; filename="walk.log"',
        b"Content-Type: text/plain",
        b"",
        b"09:00 start",
        b"09:40 done",
        b"--plainBnd1--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=plainBnd1")
    assert fd.fields["title"] == "weekly walkthrough", fd.fields
    assert fd.fields["notes"] == "first line\r\nsecond line", (
        f"multi-line field value mangled: {fd.fields['notes']!r}"
    )
    assert fd.files["log"].data == b"09:00 start\r\n09:40 done", fd.files["log"].data


def test_delimiter_lines_with_transport_padding():
    # The label-printer client pads its delimiter lines with trailing blanks;
    # RFC 2046 allows that and we have always accepted it.
    body = crlf_body(
        b"--padBnd  ",
        b'Content-Disposition: form-data; name="a"',
        b"",
        b"1",
        b"--padBnd\t",
        b'Content-Disposition: form-data; name="b"',
        b"",
        b"2",
        b"--padBnd--  ",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=padBnd")
    assert fd.fields == {"a": "1", "b": "2"}, fd.fields
    assert fd.files == {}, fd.files


def test_envelope_validation():
    for bad_ctype in ("application/json", "", "multipart/form-data",
                      "multipart/form-data; boundary="):
        try:
            parse_form_data(b"irrelevant", bad_ctype)
        except MultipartError:
            pass
        else:
            raise AssertionError(f"{bad_ctype!r} must raise MultipartError")
    try:
        parse_form_data("not bytes", "multipart/form-data; boundary=x")
    except MultipartError:
        pass
    else:
        raise AssertionError("a str body must raise MultipartError")


def test_quoted_boundary_and_empty_form():
    body = crlf_body(
        b"--qBnd77",
        b'Content-Disposition: form-data; name="only"',
        b"",
        b"value",
        b"--qBnd77--",
    )
    fd = parse_form_data(body, 'multipart/form-data; boundary="qBnd77"')
    assert fd.fields == {"only": "value"}, fd.fields

    empty = parse_form_data(b"--endBnd--\r\n", "multipart/form-data; boundary=endBnd")
    assert empty.fields == {} and empty.files == {}, (empty.fields, empty.files)


def test_default_content_type():
    body = crlf_body(
        b"--defBnd",
        b'Content-Disposition: form-data; name="blob"; filename="dump.bin"',
        b"",
        b"\x01\x02\x03",
        b"--defBnd--",
    )
    fd = parse_form_data(body, "multipart/form-data; boundary=defBnd")
    assert fd.files["blob"].content_type == "application/octet-stream", (
        fd.files["blob"].content_type
    )
    assert fd.files["blob"].data == b"\x01\x02\x03", fd.files["blob"].data


def main():
    tests = [
        test_saved_digest_survives_intact,
        test_archive_upload_and_trailing_fields,
        test_accented_text_fields,
        test_accented_filenames_and_binary_payload,
        test_ascii_forms_roundtrip,
        test_delimiter_lines_with_transport_padding,
        test_envelope_validation,
        test_quoted_boundary_and_empty_form,
        test_default_content_type,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
