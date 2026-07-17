"""Acceptance gate for the magstripe decoder.

Run under the fleet's strict flags:  python3 -bb -W error test_magstripe.py
"""

import magstripe


def main():
    rec = magstripe.parse_track(b";GC0012345678=2712A?")
    want = {"card": "GC0012345678", "expires": "2027-12", "status": "A"}
    assert rec == want, "sentinelled track decoded wrong: %r" % rec

    # The pen occasionally strips sentinels itself; both forms must decode.
    rec2 = magstripe.parse_track(b"MC9900112233=2603S")
    want2 = {"card": "MC9900112233", "expires": "2026-03", "status": "S"}
    assert rec2 == want2, "bare track decoded wrong: %r" % rec2

    assert magstripe.is_active(rec) is True, "status A card must read active"
    assert magstripe.is_active(rec2) is False, "status S card must read blocked"

    assert magstripe.tier(rec) == "gift", magstripe.tier(rec)
    assert magstripe.tier(rec2) == "merch", magstripe.tier(rec2)
    other = {"card": "XX0000000000", "expires": "2027-01", "status": "A"}
    assert magstripe.tier(other) == "unknown", magstripe.tier(other)

    assert magstripe.batch_done(b"*\r\n") is True, "end-of-batch marker missed"
    assert magstripe.batch_done(b"OK 4\r\n") is False, "status line is not a marker"
    assert magstripe.batch_done(b"  *  \r\n") is True, "padded marker missed"

    print("ok")


if __name__ == "__main__":
    main()
