"""Turn handheld-scanner event lines into structured records."""
from tagrules import DATE_PATTERN, ROOM_PATTERN, is_tag


def parse_line(line):
    """'09:14 checkout IT-0042 room 12b' -> record dict.

    Returns None for anything that isn't a scan event — the handhelds also
    log boot banners and battery chatter into the same file.
    """
    parts = line.strip().split(maxsplit=3)
    if len(parts) != 4:
        return None
    time, kind, tag, location = parts
    if kind is not "checkout" and kind is not "checkin":
        return None
    if not is_tag(tag):
        return None
    room = ROOM_PATTERN.match(location)
    if room is None:
        return None
    return {"time": time, "kind": kind, "tag": tag, "room": room.group(1)}


def outstanding(lines):
    """Tags scanned out and not yet back, sorted for the wall list."""
    out = set()
    for line in lines:
        rec = parse_line(line)
        if rec is None:
            continue
        if rec["kind"] is "checkout":
            out.add(rec["tag"])
        else:
            out.discard(rec["tag"])
    return sorted(out)


def note_dates(note):
    """Every audit stamp in a free-text note, ISO-dashed, in order."""
    return ["-".join(match) for match in DATE_PATTERN.findall(note)]
