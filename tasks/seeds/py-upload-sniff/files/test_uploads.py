"""Acceptance checks for uploads.py. Run: python3 test_uploads.py"""
from uploads import (DEFAULT_MAX_BYTES, ExtensionError, TooLargeError,
                     UploadError, UploadValidator, split_ext)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00"
GIF87 = b"GIF87a\x01\x00\x01\x00"
GIF89 = b"GIF89a\x01\x00\x01\x00"
PDF = b"%PDF-1.7\n%%EOF\n"
ZIPB = b"PK\x03\x04\x14\x00\x00\x00"
EXE = b"MZ\x90\x00\x03\x00\x00\x00"


# ---------------------------------------------------------------- existing

def test_split_ext():
    assert split_ext("photo.PNG") == "png"
    assert split_ext("dir/sub/file.TXT") == "txt"
    assert split_ext("archive.tar.gz") == "gz"
    assert split_ext(".bashrc") == ""
    assert split_ext(".config.yml") == "yml"
    assert split_ext("README") == ""


def test_size_limits():
    v = UploadValidator(max_bytes=8)
    assert v.validate("a.txt", b"12345678") == "txt"
    try:
        v.validate("a.txt", b"123456789")
        assert False, "oversized upload accepted"
    except TooLargeError:
        pass
    assert UploadValidator().max_bytes == DEFAULT_MAX_BYTES
    try:
        UploadValidator(max_bytes=0)
        assert False, "max_bytes=0 accepted"
    except ValueError:
        pass


def test_extension_rules():
    v = UploadValidator()
    assert v.validate("PHOTO.PNG", b"x") == "png"
    for name in ["run.exe", "README", ".bashrc", "", "   "]:
        try:
            v.validate(name, b"x")
            assert False, "accepted filename %r" % (name,)
        except ExtensionError:
            pass


def test_custom_allowed_set():
    v = UploadValidator(allowed={"CSV"})
    assert v.validate("data.csv", b"a,b\n") == "csv"
    try:
        v.validate("img.png", b"x")
        assert False, "png accepted by csv-only validator"
    except ExtensionError:
        pass


def test_no_policy_means_no_sniffing():
    # Without a policy the validator trusts the extension, as it always has.
    assert UploadValidator().validate("fake.png", EXE) == "png"


# --------------------------------- feature: magic-byte sniffing + policy

def test_sniff_table():
    from uploads import sniff
    assert sniff(PNG) == "png"
    assert sniff(JPEG) == "jpeg"
    assert sniff(GIF87) == "gif"
    assert sniff(GIF89) == "gif"
    assert sniff(PDF) == "pdf"
    assert sniff(ZIPB) == "zip"
    assert sniff(b"") is None
    assert sniff(b"GIF8") is None          # shorter than any gif signature
    assert sniff(b"GIF88a\x00\x00") is None
    assert sniff(PNG[:7]) is None          # truncated signature
    assert sniff(b"hello world") is None


def test_mismatch_detected():
    from uploads import MagicMismatchError, SniffPolicy
    v = UploadValidator(policy=SniffPolicy())
    assert v.validate("avatar.png", PNG) == "png"
    for name, data in [("avatar.jpg", PNG), ("slides.pdf", ZIPB),
                       ("anim.gif", JPEG)]:
        try:
            v.validate(name, data)
            assert False, "mismatched %r accepted" % (name,)
        except MagicMismatchError:
            pass


def test_container_formats_map_to_zip():
    from uploads import MagicMismatchError, SniffPolicy
    v = UploadValidator(policy=SniffPolicy())
    assert v.validate("report.docx", ZIPB) == "docx"
    try:
        v.validate("report.docx", PDF)
        assert False, "pdf bytes accepted as docx"
    except MagicMismatchError:
        pass


def test_unknown_signature_policy():
    from uploads import (MagicMismatchError, SniffPolicy,
                         UnknownSignatureError)
    strict = UploadValidator(policy=SniffPolicy())
    try:
        strict.validate("shell.png", EXE)
        assert False, "unrecognized bytes accepted as png"
    except UnknownSignatureError:
        pass
    lax = UploadValidator(policy=SniffPolicy(require_known=False))
    assert lax.validate("shell.png", EXE) == "png"
    try:
        lax.validate("logo.gif", PNG)  # positive mismatch still fails
        assert False, "png bytes accepted as gif under lax policy"
    except MagicMismatchError:
        pass


def test_unmapped_extensions_skip_sniffing():
    from uploads import SniffPolicy
    v = UploadValidator(policy=SniffPolicy())
    assert v.validate("notes.txt", EXE) == "txt"


def test_custom_extension_map():
    from uploads import MagicMismatchError, SniffPolicy
    pol = SniffPolicy(extension_types={"png": "png"})
    v = UploadValidator(policy=pol)
    assert v.validate("x.png", PNG) == "png"
    assert v.validate("photo.jpg", PNG) == "jpg"  # jpg unmapped here
    try:
        v.validate("x.png", JPEG)
        assert False, "jpeg bytes accepted as png"
    except MagicMismatchError:
        pass


def test_policy_runs_after_existing_checks():
    from uploads import SniffPolicy
    small = UploadValidator(max_bytes=10, policy=SniffPolicy())
    try:
        small.validate("big.png", PNG)
        assert False, "oversized upload accepted"
    except TooLargeError:
        pass
    v = UploadValidator(policy=SniffPolicy())
    try:
        v.validate("run.exe", EXE)
        assert False, "disallowed extension reached sniffing"
    except ExtensionError:
        pass


def test_new_errors_are_upload_errors():
    from uploads import MagicMismatchError, SniffPolicy, UnknownSignatureError
    assert issubclass(MagicMismatchError, UploadError)
    assert issubclass(UnknownSignatureError, UploadError)
    v = UploadValidator(policy=SniffPolicy())
    try:
        v.validate("avatar.jpg", PNG)
        assert False, "mismatch not raised"
    except UploadError:
        pass  # callers that catch UploadError keep working


EXISTING = [
    test_split_ext,
    test_size_limits,
    test_extension_rules,
    test_custom_allowed_set,
    test_no_policy_means_no_sniffing,
]

FEATURE = [
    test_sniff_table,
    test_mismatch_detected,
    test_container_formats_map_to_zip,
    test_unknown_signature_policy,
    test_unmapped_extensions_skip_sniffing,
    test_custom_extension_map,
    test_policy_runs_after_existing_checks,
    test_new_errors_are_upload_errors,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
