"""Acceptance tests for the IBAN toolkit. Run: python3 test_iban.py"""
import iban

VALID = [
    "AT611904300234573201",
    "BE68539007547034",
    "CH9300762011623852957",
    "DE89370400440532013000",
    "ES9121000418450200051332",
    "FR1420041010050500013M02606",
    "GB29NWBK60161331926819",
    "IT60X0542811101000000123456",
    "NL91ABNA0417164300",
    "PL61109010140000071219812874",
]


def expect_error(fn, *args, needles=()):
    try:
        fn(*args)
    except iban.IBANError as e:
        msg = str(e).lower()
        for n in needles:
            assert n.lower() in msg, f"error for {args!r} should mention {n!r}, got: {e}"
        return e
    raise AssertionError(f"{fn.__name__}{args!r} should have raised IBANError")


def main():
    # IBANError is catchable as a ValueError
    assert issubclass(iban.IBANError, ValueError)

    # --- validate: canonical electronic form back for every supported country
    for v in VALID:
        assert iban.validate(v) == v, v
        assert iban.is_valid(v) is True, v

    # separators and case are forgiven on input
    assert iban.validate("de89 3704 0044 0532 0130 00") == "DE89370400440532013000"
    assert iban.validate("GB29-NWBK-6016-1331-9268-19") == "GB29NWBK60161331926819"
    assert iban.validate(" nl91-abna 0417  1643-00 ") == "NL91ABNA0417164300"

    # --- each failure mode, with a reason a human can act on
    # single-character typo is caught by mod-97
    expect_error(iban.validate, "DE89370400440532013001", needles=["checksum"])
    expect_error(iban.validate, "GB29NWBK60161331926818", needles=["checksum"])
    assert iban.is_valid("BE68539007547035") is False

    # transposed neighbours (the classic manual-entry slip) are caught too
    expect_error(iban.validate, "DE89370400440532031000", needles=["checksum"])

    # unsupported / unknown country code, named in the message
    expect_error(iban.validate, "XX82WEST12345698765432", needles=["country", "XX"])

    # wrong length for a known country, expected length in the message
    expect_error(iban.validate, "DE893704004405320130", needles=["length", "22"])
    expect_error(iban.validate, "NL91ABNA041716430011", needles=["length", "18"])

    # BBAN structure violations (length and checksum slots untouched)
    expect_error(iban.validate, "GB82123412345698765432", needles=["structure"])
    expect_error(iban.validate, "NL02ABNA04171643AA", needles=["structure"])

    # garbage characters are named
    expect_error(iban.validate, "DE89 3704*0044 0532 0130 00", needles=["character", "*"])
    expect_error(iban.validate, "DE89_370400440532013000", needles=["character", "_"])

    # country slot must be letters; check-digit slot must be digits
    expect_error(iban.validate, "D889370400440532013000", needles=["country"])
    expect_error(iban.validate, "DE8A370400440532013000", needles=["check digit"])

    # hopelessly short input
    expect_error(iban.validate, "", needles=["short"])
    expect_error(iban.validate, "DE8", needles=["short"])

    # --- formatting
    assert iban.format_electronic("de89 3704 0044 0532 0130 00") == "DE89370400440532013000"
    assert iban.format_paper("DE89370400440532013000") == "DE89 3704 0044 0532 0130 00"
    # 27 chars leaves a trailing group of 3
    assert iban.format_paper("fr14-2004-1010-0505-0001-3m02-606") == "FR14 2004 1010 0505 0001 3M02 606"
    # 21 chars leaves a trailing single character
    assert iban.format_paper("CH9300762011623852957") == "CH93 0076 2011 6238 5295 7"
    # formatters refuse invalid input rather than prettifying garbage
    expect_error(iban.format_paper, "DE89370400440532013001", needles=["checksum"])
    expect_error(iban.format_electronic, "XX82WEST12345698765432", needles=["country"])

    # --- BBAN extraction
    assert iban.bban("DE89370400440532013000") == "370400440532013000"
    assert iban.bban("gb29 nwbk 6016 1331 9268 19") == "NWBK60161331926819"
    expect_error(iban.bban, "GB29NWBK60161331926818", needles=["checksum"])

    # --- computing check digits
    assert iban.with_check_digits("DE", "370400440532013000") == "DE89370400440532013000"
    assert iban.with_check_digits("nl", "ABNA0417164300") == "NL91ABNA0417164300"
    # computed digits are zero-padded to two characters
    made = iban.with_check_digits("DE", "370400440532013003")
    assert made == "DE08370400440532013003", made
    assert iban.validate(made) == made
    # every produced IBAN must itself validate
    for v in VALID:
        assert iban.with_check_digits(v[:2], v[4:]) == v
    expect_error(iban.with_check_digits, "XX", "12345678", needles=["country", "XX"])
    expect_error(iban.with_check_digits, "DE", "37040044053201300", needles=["length"])
    expect_error(iban.with_check_digits, "GB", "123412345698765432", needles=["structure"])

    # --- batch validation keeps order and reports per-item reasons
    batch = iban.validate_batch([
        "de89 3704 0044 0532 0130 00",
        "DE89370400440532013001",
        "XX82WEST12345698765432",
        "GB29-NWBK-6016-1331-9268-19",
        "NL91ABNA041716430011",
    ])
    assert len(batch) == 5
    assert [r["input"] for r in batch] == [
        "de89 3704 0044 0532 0130 00",
        "DE89370400440532013001",
        "XX82WEST12345698765432",
        "GB29-NWBK-6016-1331-9268-19",
        "NL91ABNA041716430011",
    ]
    assert batch[0]["ok"] is True and batch[0]["iban"] == "DE89370400440532013000"
    assert batch[3]["ok"] is True and batch[3]["iban"] == "GB29NWBK60161331926819"
    for i, needle in [(1, "checksum"), (2, "country"), (4, "length")]:
        assert batch[i]["ok"] is False, batch[i]
        assert "iban" not in batch[i]
        assert needle in batch[i]["error"].lower(), batch[i]
    assert iban.validate_batch([]) == []

    print("ok")


if __name__ == "__main__":
    main()
