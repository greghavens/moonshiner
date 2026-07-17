"""scanfeed.py — process package-scan feeds from the depot hand scanners.

Each feed line is ``TIMESTAMP|EVENT|PACKAGE|DEPOT``, e.g.::

    2026-07-01T08:30:00|PICKUP|PKG-1041|RIVERSIDE

Blank lines and ``#`` comments are allowed (the depot tooling writes a
comment header). Hand scanners double-fire constantly, QA seeds the feed
with synthetic ``QA-`` packages, and about one line in a thousand is
mangled by the serial bridge — so processing means: parse, drop the QA
traffic, drop repeat scans, and count everything for the shift summary.

Everything is wired through callbacks. Callers hand in ``on_scan`` (fires
per surviving record) plus optional ``on_bad`` and ``on_done``.
"""

EVENTS = ("PICKUP", "SCAN", "LOAD", "DELIVER")


def process_scan_lines(lines, on_scan, on_bad=None, on_done=None):
    """Run the whole feed through the callback chain.

    ``on_scan(record)`` gets each surviving record as a dict with keys
    ``ts``, ``event``, ``package``, ``depot`` (event/package/depot are
    normalized to upper case). ``on_bad(lineno, line, reason)`` fires per
    malformed line; ``on_done(summary)`` fires once at the end.
    """
    stats = {
        "events": {},
        "packages": set(),
        "delivered": set(),
        "bad_lines": 0,
        "duplicates": 0,
        "qa_dropped": 0,
    }
    last_seen = {}

    def report_bad(lineno, raw, reason):
        stats["bad_lines"] += 1
        if on_bad is not None:
            on_bad(lineno, raw, reason)

    def parse_line(lineno, raw, then):
        parts = raw.split("|")
        if len(parts) != 4:
            report_bad(lineno, raw, "expected 4 fields")
            return
        ts, event, package, depot = (p.strip() for p in parts)
        if not ts or not package or not depot:
            report_bad(lineno, raw, "empty field")
            return
        event = event.upper()
        if event not in EVENTS:
            report_bad(lineno, raw, "unknown event: %s" % event)
            return
        then({"ts": ts, "event": event,
              "package": package.upper(), "depot": depot.upper()})

    def process_one(lineno, raw):
        def after_parse(record):
            def after_qa(rec):
                def after_dedupe(fresh):
                    stats["events"][fresh["event"]] = \
                        stats["events"].get(fresh["event"], 0) + 1
                    stats["packages"].add(fresh["package"])
                    if fresh["event"] == "DELIVER":
                        stats["delivered"].add(fresh["package"])
                    on_scan(fresh)

                key = (rec["event"], rec["depot"])
                if last_seen.get(rec["package"]) == key:
                    stats["duplicates"] += 1
                    return
                last_seen[rec["package"]] = key
                after_dedupe(rec)

            if record["package"].startswith("QA-"):
                stats["qa_dropped"] += 1
                return
            after_qa(record)

        parse_line(lineno, raw, after_parse)

    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        process_one(lineno, stripped)

    if on_done is not None:
        on_done({
            "events": dict(stats["events"]),
            "packages": len(stats["packages"]),
            "delivered": sorted(stats["delivered"]),
            "bad_lines": stats["bad_lines"],
            "duplicates": stats["duplicates"],
            "qa_dropped": stats["qa_dropped"],
        })


def process_scan_file(path, on_scan, on_bad=None, on_done=None):
    """Convenience wrapper the shift-summary cron uses."""
    with open(path, "r", encoding="utf-8") as fh:
        process_scan_lines(fh, on_scan, on_bad=on_bad, on_done=on_done)
