"""Lane roster for the club swim meets.

Entries come off the sign-up sheet as {"name", "seed_time"} dicts;
seed_time is seconds, or None when the swimmer has never been timed.
Timed swimmers race in seeded heats. Untimed swimmers may swim
exhibition when the meet allows it; otherwise they sit the event out.
"""


def split_entries(entries, allow_exhibition):
    """Split sign-ups into (seeded_entries, exhibition_names).

    Seeded entries keep their full dicts; exhibition swimmers are just
    names on the program. Untimed swimmers at a meet without exhibition
    heats simply don't appear anywhere.
    """
    seeded = []
    exhibition = []
    for entry in entries:
        if entry["seed_time"] is None:
            if allow_exhibition:
                exhibition.append(entry["name"])
            else:
                seeded.append(entry)
    return seeded, exhibition


def pack_heats(seeded, lanes):
    """Chunk seeded entries into heats of `lanes`, fastest swimmers first."""
    ordered = sorted(seeded, key=lambda e: e["seed_time"])
    heats = []
    for start in range(0, len(ordered), lanes):
        heats.append([e["name"] for e in ordered[start:start + lanes]])
    return heats


def format_program(heats, exhibition):
    """Render the printed program, one line per lane assignment."""
    lines = []
    for number, heat in enumerate(heats, start=1):
        lines.append(f"Heat {number}")
        for lane, name in enumerate(heat, start=1):
			lines.append(f"  lane {lane}: {name}")
		lines.append("")
    if exhibition:
        lines.append("Exhibition")
        for name in sorted(exhibition):
            lines.append(f"  {name}")
        lines.append("")
    return lines


def meet_program(entries, lanes, allow_exhibition):
    """Full pipeline: split sign-ups, seed the heats, print the program."""
    seeded, exhibition = split_entries(entries, allow_exhibition)
    heats = pack_heats(seeded, lanes)
    return format_program(heats, exhibition)
