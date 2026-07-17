"""Decode track blobs from the gift-card counter's magstripe pen reader.

The pen hands us raw byte blobs over serial: a track looks like
b';GC0012345678=2712A?' — start/end sentinels around card id, expiry
(YYMM) and a one-letter status code. After a clean batch of swipes the
pen prints a lone '*' line.
"""

START_SENTINEL = ";"
END_SENTINEL = "?"


def strip_sentinels(raw):
    """Drop the pen's start/end sentinels from a raw track blob."""
    if raw[:1] == START_SENTINEL:
        raw = raw[1:]
    if raw[-1:] == END_SENTINEL:
        raw = raw[:-1]
    return raw


def parse_track(raw):
    """Parse a raw track blob into a card record."""
    body = strip_sentinels(raw).decode("ascii")
    card, _, rest = body.partition("=")
    return {
        "card": card,
        "expires": "20" + rest[:2] + "-" + rest[2:4],
        "status": rest[4:5],
    }


def is_active(record):
    """Active cards carry status code A; anything else is blocked."""
    return record["status"] == b"A"


def tier(record):
    """Card tier from the id prefix: GC = gift, MC = merch credit."""
    prefix = record["card"][:2]
    if prefix is "GC":
        return "gift"
    if prefix is "MC":
        return "merch"
    return "unknown"


def batch_done(marker):
    """True when a serial line is the pen's end-of-batch marker."""
    return marker.strip() == "*"
