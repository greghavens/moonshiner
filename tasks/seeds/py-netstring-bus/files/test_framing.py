"""Behavior checks for netstring framing. Run: python3 test_framing.py"""
from framing import FrameError, decode_all, decode_one, encode


def main():
    # ASCII round-trip.
    frame = encode("ping")
    assert isinstance(frame, bytes), "frames must be bytes"
    msg, rest = decode_one(frame)
    assert (msg, rest) == ("ping", b""), f"got {(msg, rest)!r}"

    # Non-ASCII text must round-trip exactly the same way.
    for text in ("café", "naïve response", "温度=22°C", "emoji 🚀 ok"):
        msg, rest = decode_one(encode(text))
        assert msg == text, f"round-trip mangled {text!r} -> {msg!r}"
        assert rest == b"", f"decoder left {rest!r} behind for {text!r}"

    # A stream of mixed frames stays in sync.
    stream = encode("café") + encode("ok") + encode("再见")
    msgs = decode_all(stream)
    assert msgs == ["café", "ok", "再见"], f"stream desynced: {msgs!r}"

    # Malformed input is still rejected.
    for bad in (b"abc", b"5:hi,", b"2:hi;", b"4:hi"):
        try:
            decode_one(bad)
            raise AssertionError(f"{bad!r} must be rejected")
        except FrameError:
            pass

    # Empty message is legal.
    assert decode_all(encode("")) == [""]

    print("all checks passed")


if __name__ == "__main__":
    main()
