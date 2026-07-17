"""Acceptance tests for the base32 codec with check symbol. Run: python3 test_b32check.py"""


def main():
    from b32check import encode, decode, DecodeError, ChecksumError

    # ChecksumError must be catchable as a DecodeError
    assert issubclass(ChecksumError, DecodeError)
    assert issubclass(DecodeError, ValueError)

    # -- known vectors (payload = RFC-4648 bit packing over our alphabet,
    #    final char = mod-37 check symbol) --
    vectors = {
        b"": "0",
        b"f": "CRW",
        b"fo": "CSQGV",
        b"foo": "CSQPYY",
        b"foob": "CSQPYRG8",
        b"fooba": "CSQPYRK1U",
        b"foobar": "CSQPYRK1E86",
        b"\x00": "000",
        b"\x00\x00": "00000",
        b"\xff\xff": "ZZZG8",
        b"hello world": "D1JPRV3F41VPYWKCCGS",
    }
    for data, text in vectors.items():
        assert encode(data) == text, (data, encode(data), text)
        assert decode(text) == data, (text, decode(text), data)

    # -- round trip over many lengths --
    for n in range(0, 64):
        data = bytes((7 * i + n) % 256 for i in range(n))
        assert decode(encode(data)) == data, n

    # -- decoding is forgiving to humans: case, hyphens, I/L/O confusion --
    assert decode("csqgv") == b"fo"
    assert decode("CSQ-GV") == b"fo"
    assert decode("csq-pyrk-1e86") == b"foobar"
    assert decode("OOO") == b"\x00"          # O reads as 0
    assert decode("D1JPRV3F41VPYWKCCGS".replace("1", "l")) == b"hello world"
    assert decode("D1JPRV3F41VPYWKCCGS".replace("1", "I")) == b"hello world"
    # but encode always emits canonical text
    assert encode(b"\x00") == "000"

    # -- every single-character substitution is caught --
    text = "CSQPYRK1E86"                           # b"foobar"
    full = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    for i in range(len(text)):
        for repl in full:
            if repl == text[i]:
                continue
            corrupted = text[:i] + repl + text[i + 1:]
            try:
                decode(corrupted)
                assert False, f"corruption not detected: {corrupted!r}"
            except DecodeError:
                pass

    # -- a checksum mismatch specifically raises ChecksumError --
    try:
        decode("DSQGV")  # first payload char flipped, bit padding still clean
        assert False, "should raise ChecksumError"
    except ChecksumError:
        pass
    try:
        decode("CSQG$")  # check symbol itself wrong
        assert False, "should raise ChecksumError"
    except ChecksumError:
        pass

    # -- adjacent transposition is caught too --
    try:
        decode("1DJPRV3F41VPYWKCCGS")
        assert False, "transposition not detected"
    except DecodeError:
        pass

    # -- structurally invalid text --
    for bad in ["",            # no room for a check symbol
                "00",          # 1 payload char can't happen (8 bits need 2)
                "0000",        # nor 3
                "0000000",     # nor 6
                "C!W",         # character not in any alphabet
                "CUQGV",       # U is only ever a check symbol
                "C*W"]:        # * is only ever a check symbol
        try:
            decode(bad)
            assert False, f"decode({bad!r}) should raise DecodeError"
        except DecodeError:
            pass

    # -- nonzero padding bits are corruption, not slack --
    try:
        decode("CSW")  # b"f" is CRW; S sets a padding bit R leaves at zero
        assert False, "nonzero padding must be rejected"
    except DecodeError:
        pass

    print("ok")


if __name__ == "__main__":
    main()
