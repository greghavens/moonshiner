"""Import helper for camera memory cards.

Cameras name frames <PREFIX>_<number>.<ext> — IMG_4021.jpg, DSC_0098.NEF,
clip_7.mov. The import tool uses this module to lay out the contact sheet
in shoot order, spot frames that never made it off the card, and pick the
filename for the next frame when we append externally edited exports back
into a sequence.
"""
import re

_NAME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)_(\d+)\.([A-Za-z0-9]+)$")


def split_name(filename):
    """(prefix, frame-number string, lowercased ext) for a sequence file."""
    match = _NAME_RE.match(filename)
    if match is None:
        raise ValueError(f"not a sequence filename: {filename!r}")
    prefix, number, ext = match.groups()
    return prefix, number, ext.lower()


def shoot_order(filenames):
    """All frames in card order: by sequence prefix, then frame number."""
    keyed = []
    for filename in filenames:
        prefix, number, _ = split_name(filename)
        keyed.append(((prefix, number), filename))
    keyed.sort()
    return [filename for _, filename in keyed]


def sequences(filenames):
    """Frames grouped by sequence prefix, each group in frame order."""
    groups = {}
    for filename in shoot_order(filenames):
        prefix, _, _ = split_name(filename)
        groups.setdefault(prefix, []).append(filename)
    return groups


def missing_frames(filenames, prefix):
    """Frame numbers absent between the first and last shot of a sequence.

    This is the "did the card eat anything?" report: a gap usually means a
    frame was deleted in-camera or lost during a previous import.
    """
    frames = []
    for filename in shoot_order(filenames):
        name_prefix, number, _ = split_name(filename)
        if name_prefix == prefix:
            frames.append(number)
    gaps = []
    for prev, cur in zip(frames, frames[1:]):
        gaps.extend(range(int(prev) + 1, int(cur)))
    return gaps


def next_name(filenames, prefix, ext):
    """Filename for the frame after the last one in a sequence.

    Keeps the zero-padding of the highest existing frame (DSC_0099 ->
    DSC_0100), growing the number naturally when it needs another digit.
    A sequence with no frames yet starts at a four-digit 1.
    """
    numbers = []
    for filename in filenames:
        name_prefix, number, _ = split_name(filename)
        if name_prefix == prefix:
            numbers.append(number)
    if not numbers:
        return f"{prefix}_0001.{ext}"
    last = max(numbers)
    following = str(int(last) + 1).rjust(len(last), "0")
    return f"{prefix}_{following}.{ext}"
