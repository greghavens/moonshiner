"""Netstring framing for the local IPC bus.

Wire format, one frame per message:

    <len>:<payload>,

where <len> is the payload's size in bytes as ASCII digits and <payload>
is UTF-8 text. Frames are concatenated on the wire; the decoder peels them
off the front of a bytes buffer one at a time.
"""


class FrameError(ValueError):
    """The buffer does not start with a well-formed frame."""


def encode(message):
    """Frame one text message for the wire; returns bytes."""
    header = "%d:" % len(message)
    return header.encode("ascii") + message.encode("utf-8") + b","


def decode_one(buf):
    """Peel one frame off the front of *buf*.

    Returns (message, rest_of_buffer). Raises FrameError on anything
    malformed: missing/garbled length header, truncated payload, or a
    frame that does not end with the ',' terminator.
    """
    head, sep, rest = buf.partition(b":")
    if not sep:
        raise FrameError("no length header")
    if not head.isdigit():
        raise FrameError("bad length header %r" % head)
    n = int(head)
    if len(rest) < n + 1:
        raise FrameError("truncated frame")
    payload, terminator, remainder = rest[:n], rest[n:n + 1], rest[n + 1:]
    if terminator != b",":
        raise FrameError("missing terminator after %d-byte payload" % n)
    return payload.decode("ascii"), remainder


def decode_all(buf):
    """Decode every complete frame in *buf*; raises FrameError if bytes
    are left over that do not form a whole frame."""
    messages = []
    while buf:
        message, buf = decode_one(buf)
        messages.append(message)
    return messages
