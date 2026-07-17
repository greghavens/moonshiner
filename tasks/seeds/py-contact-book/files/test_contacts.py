"""Acceptance tests for the contact book. Run: python3 test_contacts.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def contacts(book, *args):
    return subprocess.run([sys.executable, "contacts.py", "--book", book, *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env=ENV, timeout=30)


def main():
    tmp = tempfile.mkdtemp(dir=".")
    book = os.path.join(tmp, "book.json")
    other = os.path.join(tmp, "other.json")
    try:
        # searching an empty/missing book: no output, exit 1, file not created
        r = contacts(book, "search", "anyone")
        assert r.returncode == 1 and r.stdout == "", (r.returncode, r.stdout, r.stderr)
        assert not os.path.exists(book), "search must not create the book"

        # add four contacts (silent)
        for args in [("add", "José García", "--email", "Jose@Garcia.ES",
                      "--phone", "+34-600-111-222"),
                     ("add", "Zoë Quinn", "--phone", "555-0100"),
                     ("add", "Jürgen Groß", "--email", "jg@example.de"),
                     ("add", "Ana María López")]:
            r = contacts(book, *args)
            assert r.returncode == 0 and r.stdout == "", (args, r.returncode, r.stderr)
        with open(book, encoding="utf-8") as f:
            json.load(f)  # book file is real JSON

        # case- and diacritic-insensitive search, original spelling preserved
        r = contacts(book, "search", "jose garcia")
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.splitlines() == ["José García <Jose@Garcia.ES> +34-600-111-222"], r.stdout
        r = contacts(book, "search", "GARCÍA")
        assert r.stdout.splitlines() == ["José García <Jose@Garcia.ES> +34-600-111-222"], r.stdout
        r = contacts(book, "search", "zoe")
        assert r.stdout.splitlines() == ["Zoë Quinn 555-0100"], r.stdout

        # proper case folding: ß matches ss
        r = contacts(book, "search", "gross")
        assert r.stdout.splitlines() == ["Jürgen Groß <jg@example.de>"], r.stdout

        # email is searched too
        r = contacts(book, "search", "example.de")
        assert r.stdout.splitlines() == ["Jürgen Groß <jg@example.de>"], r.stdout

        # multiple hits sorted by normalized name (email counts as a match source)
        r = contacts(book, "search", "a")
        assert r.stdout.splitlines() == [
            "Ana María López",
            "José García <Jose@Garcia.ES> +34-600-111-222",
            "Jürgen Groß <jg@example.de>",
        ], r.stdout

        # no matches: grep-style exit 1
        r = contacts(book, "search", "nobody")
        assert r.returncode == 1 and r.stdout == "", (r.returncode, r.stdout)

        # ---- build a second book through the same CLI, then merge
        assert contacts(other, "add", "Jose Garcia",
                        "--phone", "+34-999-888-777").returncode == 0
        assert contacts(other, "add", "Zoë Quinn",
                        "--email", "zq@mail.com").returncode == 0
        assert contacts(other, "add", "Marta Silva",
                        "--email", "marta@silva.pt").returncode == 0
        assert contacts(other, "add", "José García",
                        "--email", "different@garcia.es").returncode == 0

        r = contacts(book, "merge", other)
        assert r.returncode == 0, (r.returncode, r.stderr)
        assert r.stdout.strip() == "added 2, merged 2", r.stdout

        # merged contact gained the missing email but kept its phone
        r = contacts(book, "search", "zoe")
        assert r.stdout.splitlines() == ["Zoë Quinn <zq@mail.com> 555-0100"], r.stdout

        # same name + different email is a different person; existing fields won
        r = contacts(book, "search", "garcia")
        assert r.stdout.splitlines() == [
            "José García <different@garcia.es>",
            "José García <Jose@Garcia.ES> +34-600-111-222",
        ], ("dedup must not eat distinct-email namesakes; original phone stays", r.stdout)

        # merging the same file again is idempotent
        r = contacts(book, "merge", other)
        assert r.stdout.strip() == "added 0, merged 4", r.stdout

        # merge of a missing file is a usage error
        r = contacts(book, "merge", os.path.join(tmp, "ghost.json"))
        assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)

        # ---- vCard export: exact bytes, CRLF everywhere, UTF-8
        vcf = os.path.join(tmp, "out.vcf")
        r = contacts(book, "export", vcf)
        assert r.returncode == 0, (r.returncode, r.stderr)
        with open(vcf, "rb") as f:
            data = f.read()
        expected_lines = [
            "BEGIN:VCARD", "VERSION:3.0", "FN:Ana María López", "END:VCARD",
            "BEGIN:VCARD", "VERSION:3.0", "FN:José García",
            "EMAIL:different@garcia.es", "END:VCARD",
            "BEGIN:VCARD", "VERSION:3.0", "FN:José García",
            "EMAIL:Jose@Garcia.ES", "TEL:+34-600-111-222", "END:VCARD",
            "BEGIN:VCARD", "VERSION:3.0", "FN:Jürgen Groß",
            "EMAIL:jg@example.de", "END:VCARD",
            "BEGIN:VCARD", "VERSION:3.0", "FN:Marta Silva",
            "EMAIL:marta@silva.pt", "END:VCARD",
            "BEGIN:VCARD", "VERSION:3.0", "FN:Zoë Quinn",
            "EMAIL:zq@mail.com", "TEL:555-0100", "END:VCARD",
        ]
        expected = "".join(line + "\r\n" for line in expected_lines).encode("utf-8")
        assert data == expected, f"vcf mismatch:\n{data!r}\n----\n{expected!r}"

        # usage errors
        r = contacts(book, "obliterate")
        assert r.returncode == 2, (r.returncode, r.stderr)
        r = contacts(book, "search")
        assert r.returncode == 2, (r.returncode, r.stderr)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("all contact book checks passed")


if __name__ == "__main__":
    main()
