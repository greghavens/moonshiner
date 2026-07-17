"""Acceptance checks for the scanfeed generator-pipeline API.

Run: python3 test_scanfeed.py

These tests exercise the post-refactor surface: lazy generator stages
(iter_records / drop_qa / dedupe), the summarize sink, and the run()
composition. Every expected record, reason string and summary number
below was captured from the callback implementation on the same feed —
the pipeline must reproduce them exactly.
"""
from scanfeed import dedupe, drop_qa, iter_records, run, summarize


FEED = [
    "# depot feed 2026-07-01 — riverside/dockside",
    "",
    "2026-07-01T06:55:00|PICKUP|pkg-1041|riverside",
    "2026-07-01T06:55:04|pickup|PKG-1041|RIVERSIDE",
    "2026-07-01T07:10:00|SCAN|PKG-1041|DOCKSIDE",
    "2026-07-01T07:11:00|SCAN|QA-0001|DOCKSIDE",
    "2026-07-01T07:15:00|SCAN|PKG-2100|DOCKSIDE",
    "2026-07-01T07:15:02|SCAN|PKG-2100|DOCKSIDE",
    "2026-07-01T07:15:09|SCAN|PKG-2100|DOCKSIDE",
    "bogus line from the serial bridge",
    "2026-07-01T07:20:00|TELEPORT|PKG-2100|DOCKSIDE",
    "2026-07-01T07:22:00|LOAD|PKG-1041|DOCKSIDE",
    "2026-07-01T07:25:00|LOAD||DOCKSIDE",
    "   ",
    "2026-07-01T09:40:00|DELIVER|PKG-1041|MAPLE ST",
    "2026-07-01T09:41:00|deliver|qa-0001|MAPLE ST",
    "2026-07-01T10:02:00|DELIVER|PKG-2100|OAK AVE",
    "2026-07-01T10:02:00|DELIVER|PKG-2100|OAK AVE",
]

SURVIVORS = [
    {"ts": "2026-07-01T06:55:00", "event": "PICKUP", "package": "PKG-1041", "depot": "RIVERSIDE"},
    {"ts": "2026-07-01T07:10:00", "event": "SCAN", "package": "PKG-1041", "depot": "DOCKSIDE"},
    {"ts": "2026-07-01T07:15:00", "event": "SCAN", "package": "PKG-2100", "depot": "DOCKSIDE"},
    {"ts": "2026-07-01T07:22:00", "event": "LOAD", "package": "PKG-1041", "depot": "DOCKSIDE"},
    {"ts": "2026-07-01T09:40:00", "event": "DELIVER", "package": "PKG-1041", "depot": "MAPLE ST"},
    {"ts": "2026-07-01T10:02:00", "event": "DELIVER", "package": "PKG-2100", "depot": "OAK AVE"},
]


def _rec(ts, event, package, depot):
    return {"ts": ts, "event": event, "package": package, "depot": depot}


class ScriptedSource:
    """Iterator that counts pulls; pulling past the script is a failure."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.pulls = 0

    def __iter__(self):
        return self

    def __next__(self):
        assert self.pulls < len(self.lines), \
            "pipeline pulled past the scripted feed — stages must be lazy"
        line = self.lines[self.pulls]
        self.pulls += 1
        return line


# ----------------------------------------------------------------- stages

def test_iter_records_parses_normalizes_and_reports_bad_lines():
    bad = []
    records = list(iter_records(FEED, bad=lambda n, l, r: bad.append((n, l, r))))
    assert len(records) == 12, "parse stage should not filter QA or repeats"
    assert records[0] == _rec("2026-07-01T06:55:00", "PICKUP", "PKG-1041", "RIVERSIDE")
    assert records[1] == _rec("2026-07-01T06:55:04", "PICKUP", "PKG-1041", "RIVERSIDE")
    assert bad == [
        (10, "bogus line from the serial bridge", "expected 4 fields"),
        (11, "2026-07-01T07:20:00|TELEPORT|PKG-2100|DOCKSIDE", "unknown event: TELEPORT"),
        (13, "2026-07-01T07:25:00|LOAD||DOCKSIDE", "empty field"),
    ]


def test_bad_port_is_optional():
    assert list(iter_records(["junk with no pipes"])) == []


def test_drop_qa_filters_synthetic_packages_via_port():
    dropped = []
    kept = list(drop_qa(iter_records(FEED), dropped=dropped.append))
    assert len(kept) == 10
    assert [d["package"] for d in dropped] == ["QA-0001", "QA-0001"]
    assert all(not r["package"].startswith("QA-") for r in kept)


def test_dedupe_skips_repeats_of_the_last_kept_scan():
    duped = []
    kept = list(dedupe(drop_qa(iter_records(FEED)), duped=duped.append))
    assert kept == SURVIVORS
    assert len(duped) == 4


def test_dedupe_tracks_the_last_kept_key_per_package():
    seq = [
        _rec("t1", "SCAN", "PKG-9", "A"),
        _rec("t2", "SCAN", "PKG-9", "B"),
        _rec("t3", "SCAN", "PKG-9", "B"),
        _rec("t4", "SCAN", "PKG-9", "A"),
    ]
    kept = list(dedupe(iter(seq)))
    # the move back to depot A is a real scan, only the repeat at B is not
    assert [r["ts"] for r in kept] == ["t1", "t2", "t4"]

    interleaved = [
        _rec("t1", "SCAN", "PKG-1", "A"),
        _rec("t2", "SCAN", "PKG-2", "A"),
        _rec("t3", "SCAN", "PKG-1", "A"),
    ]
    kept = list(dedupe(iter(interleaved)))
    assert [r["ts"] for r in kept] == ["t1", "t2"], \
        "dedupe state must be per package, other packages do not reset it"


# --------------------------------------------------------------- laziness

def test_stages_are_lazy_iterators_with_zero_lookahead():
    src = ScriptedSource([
        "2026-07-02T08:00:00|SCAN|PKG-1|A",
        "2026-07-02T08:01:00|SCAN|PKG-2|A",
        "2026-07-02T08:02:00|SCAN|PKG-3|A",
        "2026-07-02T08:03:00|SCAN|PKG-4|A",
        "2026-07-02T08:04:00|SCAN|PKG-5|A",
    ])
    pipe = dedupe(drop_qa(iter_records(src)))
    assert hasattr(pipe, "__next__"), "the pipeline must be an iterator, not a list"
    first = next(pipe)
    assert first["package"] == "PKG-1"
    assert src.pulls == 1, "one record out should cost exactly one line in, got %d" % src.pulls
    next(pipe)
    next(pipe)
    assert src.pulls == 3, "three records out should cost exactly three lines in, got %d" % src.pulls


def test_each_stage_returns_an_iterator():
    for stage in (
        iter_records(["2026-07-02T08:00:00|SCAN|PKG-1|A"]),
        drop_qa(iter([])),
        dedupe(iter([])),
    ):
        assert iter(stage) is stage, "stages must return single-pass iterators"


# ------------------------------------------------------------------ sinks

def test_summarize_counts_surviving_records():
    summary = summarize(iter(SURVIVORS))
    assert summary == {
        "events": {"PICKUP": 1, "SCAN": 2, "LOAD": 1, "DELIVER": 2},
        "packages": 2,
        "delivered": ["PKG-1041", "PKG-2100"],
    }


def test_run_matches_the_shift_summary_of_the_callback_version():
    assert run(FEED) == {
        "events": {"PICKUP": 1, "SCAN": 2, "LOAD": 1, "DELIVER": 2},
        "packages": 2,
        "delivered": ["PKG-1041", "PKG-2100"],
        "bad_lines": 3,
        "duplicates": 4,
        "qa_dropped": 2,
    }


def test_run_still_reports_bad_lines_through_the_port():
    bad = []
    run(FEED, bad=lambda n, l, r: bad.append(r))
    assert bad == ["expected 4 fields", "unknown event: TELEPORT", "empty field"]


def test_run_on_an_empty_feed():
    assert run([]) == {
        "events": {},
        "packages": 0,
        "delivered": [],
        "bad_lines": 0,
        "duplicates": 0,
        "qa_dropped": 0,
    }


CHECKS = [
    test_iter_records_parses_normalizes_and_reports_bad_lines,
    test_bad_port_is_optional,
    test_drop_qa_filters_synthetic_packages_via_port,
    test_dedupe_skips_repeats_of_the_last_kept_scan,
    test_dedupe_tracks_the_last_kept_key_per_package,
    test_stages_are_lazy_iterators_with_zero_lookahead,
    test_each_stage_returns_an_iterator,
    test_summarize_counts_surviving_records,
    test_run_matches_the_shift_summary_of_the_callback_version,
    test_run_still_reports_bad_lines_through_the_port,
    test_run_on_an_empty_feed,
]


def main_check():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main_check()
