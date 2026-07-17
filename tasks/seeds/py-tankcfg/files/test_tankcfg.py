import sys

sys.dont_write_bytecode = True

import tankcfg
from tankcfg import EscapeError, decode, dump_bytes, encode, load_bytes


def expect_escape_error(text, pos):
    try:
        decode(text)
    except EscapeError as e:
        assert e.pos == pos, "wrong error position for %r: got %r, want %r" % (
            text,
            e.pos,
            pos,
        )
        return
    raise AssertionError("decode should have rejected %r" % text)


def main():
    assert issubclass(EscapeError, ValueError)

    # --- decode: the five escape forms -----------------------------------
    assert decode("plain text, no escapes") == "plain text, no escapes"
    assert decode("") == ""
    assert decode("a\\nb\\tc") == "a\nb\tc"
    assert decode("50\\\\50") == "50\\50"
    assert decode("\\x41 and \\x0a") == "A and \n"
    assert decode("\\x4f\\x4F") == "OO"  # hex digits are case-insensitive
    assert decode("\\u00e9 = \\u00E9") == "é = é"
    assert decode("\\u0041\\u005a") == "AZ"

    # surrogate pairs combine into one astral character
    assert decode("\\ud83d\\ude00") == "\U0001f600"
    assert decode("\\uD83D\\uDE00") == "\U0001f600"
    assert decode("ok \\ud83d\\ude00 ok") == "ok \U0001f600 ok"

    # --- decode: errors report the offending backslash -------------------
    expect_escape_error("ok\\q", 2)  # unknown escape
    expect_escape_error("\\T", 0)  # marker letters are lowercase only
    expect_escape_error("ab\\", 2)  # trailing backslash
    expect_escape_error("\\x4", 0)  # \x wants exactly two hex digits
    expect_escape_error("\\xg1", 0)
    expect_escape_error("price\\u12x", 5)  # \u wants exactly four hex digits
    expect_escape_error("\\ud83d later", 0)  # high surrogate with no partner
    expect_escape_error("\\ude00", 0)  # low surrogate with no partner
    expect_escape_error("\\ud83d\\u0041", 0)  # high surrogate, wrong partner

    # --- encode: default mode ---------------------------------------------
    assert encode("a\nb\tc") == "a\\nb\\tc"
    assert encode("back\\slash") == "back\\\\slash"
    assert encode("\r") == "\\x0d"
    assert encode("\x00\x01\x1f\x7f") == "\\x00\\x01\\x1f\\x7f"
    assert encode("café \U0001f600 ok") == "café \U0001f600 ok"
    assert encode('quote " tick \' ok') == 'quote " tick \' ok'
    assert encode("\udcff") == "\udcff"  # surrogateescape carriers stay put by default

    # --- encode: ascii_only mode ------------------------------------------
    assert encode("café", ascii_only=True) == "caf\\u00e9"
    assert encode("A\né", ascii_only=True) == "A\\n\\u00e9"
    assert encode("\U0001f600", ascii_only=True) == "\\ud83d\\ude00"
    assert encode("\u2028\u2029", ascii_only=True) == "\\u2028\\u2029"  # line/para separators
    try:
        encode("\ud800", ascii_only=True)
        raise AssertionError("lone surrogate must be rejected in ascii_only mode")
    except ValueError:
        pass

    # --- round trips --------------------------------------------------------
    samples = [
        "",
        "line1\nline2",
        "tab\tsep",
        "C:\\new\\table.txt",
        "café \U0001f600",
        "\x00\x01\x1f\x7f",
        "quote \" and 'tick'",
    ]
    for s in samples:
        assert decode(encode(s)) == s, "default round trip broke for %r" % s
        assert decode(encode(s, ascii_only=True)) == s, (
            "ascii_only round trip broke for %r" % s
        )
    assert decode(encode("\udcff raw")) == "\udcff raw"

    # --- bytes boundary: utf-8 + surrogateescape ----------------------------
    assert load_bytes(b"name=Neon Tetra\\tqty=6") == "name=Neon Tetra\tqty=6"
    assert dump_bytes("depth 1.5m\n") == b"depth 1.5m\\n"
    assert dump_bytes("café") == b"caf\xc3\xa9"

    raw = b"tank \xff log"  # not valid utf-8; must survive untouched
    v = load_bytes(raw)
    assert v == "tank \udcff log", repr(v)
    assert dump_bytes(v) == raw

    print("ok")


if __name__ == "__main__":
    main()
