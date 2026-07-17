"""Morning river-gauge telemetry, as published on the county hydrology feed.

Each reading is one line:

    2026-07-14 06:15 STN-042 stage=3.82m flow=124.500

The office wants readings validated, the daily peak per station, and a
tab-separated sheet the spreadsheet folks can paste straight in.
"""

import re

READING = re.compile(
    "(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}) (STN-\d+) stage=(\d+\.\d+)m flow=(\d+\.\d+)$"
)

STATION = re.compile("STN-\d{3}$")


def parse_line(line):
    m = READING.match(line.strip())
    if m is None:
        raise ValueError("unrecognised gauge line: " + repr(line))
    date, hhmm, station, stage, flow = m.groups()
    return {
        "date": date,
        "time": hhmm,
        "station": station,
        "stage": float(stage),
        "flow": float(flow),
    }


def is_station(token):
    return STATION.match(token) is not None


def readings(text):
    """All readings in a raw feed dump, skipping blanks and comment lines."""
    out = []
    for line in text.splitlines():
        if not line.strip() or re.match("\s*#", line):
            continue
        out.append(parse_line(line))
    return out


def daily_peak(lines):
    peaks = {}
    for line in lines:
        r = parse_line(line)
        prev = peaks.get(r["station"])
        if prev is None or r["stage"] > prev:
            peaks[r["station"]] = r["stage"]
    return peaks


def sheet(lines):
    rows = sorted(
        (parse_line(l) for l in lines),
        key=lambda r: (r["station"], r["time"]),
    )
    out = ["station\ttime\tstage_m\tflow"]
    for r in rows:
        out.append(
            "%s\t%s\t%.2f\t%.3f" % (r["station"], r["time"], r["stage"], r["flow"])
        )
    return "\n".join(out)
