"""Acceptance tests for logmerge.

Fixtures are written under ./logmerge_fixtures by the tests themselves; every
timestamp is fixed. Run: python3 test_logmerge.py
"""
import os
import shutil
from datetime import datetime, timezone

from logmerge import merge_files, parse_timestamp, sniff_format

FIX = "logmerge_fixtures"


def utc(y, mo, d, h, mi, s, frac=0.0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp() + frac


def write(name, text):
    path = os.path.join(FIX, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def reset_fixtures():
    if os.path.isdir(FIX):
        shutil.rmtree(FIX)
    os.makedirs(FIX)


def test_sniff_format():
    assert sniff_format("2026-03-14T09:26:53 web-1 GET / 200") == "iso"
    assert sniff_format("2026-03-14T09:26:53.250 web-1 GET / 200") == "iso"
    assert sniff_format("1773480413 worker-3 job done") == "epoch"
    assert sniff_format("1773480413.5 worker-3 job done") == "epoch"
    assert sniff_format("Mar 14 09:26:53 gate-1 sshd[411]: session opened") == "syslog"
    assert sniff_format("Mar  4 09:26:53 gate-1 sshd[411]: session opened") == "syslog"
    for junk in ("hello world", "<14>oops", "14:09:26 short time", ""):
        try:
            sniff_format(junk)
        except ValueError:
            pass
        else:
            raise AssertionError(f"sniff_format({junk!r}) should raise ValueError")


def test_parse_timestamp_exact():
    assert parse_timestamp("2026-03-14T09:26:53 m", "iso") == utc(2026, 3, 14, 9, 26, 53)
    assert parse_timestamp("2026-03-14T09:26:53.250 m", "iso") == utc(2026, 3, 14, 9, 26, 53, 0.25)
    assert parse_timestamp("1700000000.5 m", "epoch") == 1700000000.5
    assert parse_timestamp("1700000000 m", "epoch") == 1700000000.0
    assert parse_timestamp("Mar 14 09:26:53 gate m", "syslog") == utc(2026, 3, 14, 9, 26, 53)
    assert parse_timestamp("Mar  4 09:26:53 gate m", "syslog") == utc(2026, 3, 4, 9, 26, 53)
    assert parse_timestamp("Mar 14 09:26:53 gate m", "syslog",
                           assume_year=2024) == utc(2024, 3, 14, 9, 26, 53)


def test_merge_across_formats():
    reset_fixtures()
    e = utc(2026, 3, 14, 9, 26, 53)
    web = write("web.log",
                "2026-03-14T09:26:53 web-1 GET /api/users 200\n"
                "2026-03-14T09:26:53.250 web-1 GET /api/orders 200\n"
                "2026-03-14T09:27:10 web-1 GET /healthz 200\n")
    worker = write("worker.log",
                   f"{e - 3:.0f} worker-3 job 87 picked up\n"
                   f"{e + 0.5:.1f} worker-3 job 87 done\n")
    auth = write("auth.log",
                 "Mar 14 09:26:53 gate-1 sshd[411]: session opened for deploy\n"
                 "Mar 14 09:27:02 gate-1 sudo[412]: command allowed\n")

    merged = merge_files([web, worker, auth])
    got = [(x["source"], x["lineno"]) for x in merged]
    assert got == [
        (worker, 1),   # 09:26:50
        (web, 1),      # 09:26:53 (stream 0 wins the tie with auth line 1)
        (auth, 1),     # 09:26:53
        (web, 2),      # 09:26:53.250
        (worker, 2),   # 09:26:53.5
        (auth, 2),     # 09:27:02
        (web, 3),      # 09:27:10
    ], got
    assert merged[0]["ts"] == e - 3
    assert merged[1]["ts"] == e and merged[2]["ts"] == e
    assert merged[3]["ts"] == e + 0.25
    assert merged[1]["line"] == "2026-03-14T09:26:53 web-1 GET /api/users 200"
    assert not merged[1]["line"].endswith("\n"), "only the newline is stripped"


def test_tie_break_follows_paths_order_then_lineno():
    reset_fixtures()
    a = write("a.log", "2026-01-01T00:00:00 from-a first\n"
                       "2026-01-01T00:00:00 from-a second\n")
    b = write("b.log", "2026-01-01T00:00:00 from-b only\n")
    merged = merge_files([b, a])  # deliberately not alphabetical
    got = [(x["source"], x["lineno"]) for x in merged]
    assert got == [(b, 1), (a, 1), (a, 2)], got


def test_skew_window_repairs_small_disorder():
    reset_fixtures()
    burst = write("burst.log",
                  "2026-03-14T09:00:00 svc step one\n"
                  "2026-03-14T09:00:04 svc step two\n"
                  "2026-03-14T09:00:02.500 svc late flush\n"
                  "2026-03-14T09:00:05 svc step three\n")
    merged = merge_files([burst], skew=2.0)
    assert [x["lineno"] for x in merged] == [1, 3, 2, 4]
    ts = [x["ts"] for x in merged]
    assert ts == sorted(ts), "output must be globally sorted"


def test_disorder_beyond_skew_is_rejected():
    reset_fixtures()
    bad = write("bad.log",
                "2026-03-14T09:00:00 svc a\n"
                "2026-03-14T09:00:04 svc b\n"
                "2026-03-14T09:00:02.500 svc late\n")
    try:
        merge_files([bad], skew=1.0)
    except ValueError as e:
        msg = str(e)
        assert bad in msg and "line 3" in msg, msg
    else:
        raise AssertionError("disorder beyond the skew window must raise")

    # default skew is 0: any regression is an error, exact ties are fine
    regress = write("regress.log",
                    "2026-03-14T09:00:01 svc a\n"
                    "2026-03-14T09:00:00 svc early\n")
    try:
        merge_files([regress])
    except ValueError as e:
        assert regress in str(e) and "line 2" in str(e), str(e)
    else:
        raise AssertionError("regression with skew=0 must raise")

    ties = write("ties.log",
                 "2026-03-14T09:00:01 svc a\n"
                 "2026-03-14T09:00:01 svc b\n")
    assert [x["lineno"] for x in merge_files([ties])] == [1, 2]

    try:
        merge_files([ties], skew=-1)
    except ValueError:
        pass
    else:
        raise AssertionError("negative skew must raise")


def test_blank_lines_skipped_but_numbering_is_physical():
    reset_fixtures()
    spaced = write("spaced.log",
                   "2026-03-14T09:00:00 svc a\n"
                   "\n"
                   "2026-03-14T09:00:01 svc b\n")
    merged = merge_files([spaced])
    assert [x["lineno"] for x in merged] == [1, 3]


def test_format_is_sniffed_per_file_and_enforced():
    reset_fixtures()
    mixed = write("mixed.log",
                  "2026-03-14T09:00:00 svc fine\n"
                  "Mar 14 09:00:01 svc not iso\n")
    try:
        merge_files([mixed])
    except ValueError as e:
        assert mixed in str(e) and "line 2" in str(e), str(e)
    else:
        raise AssertionError("a line that breaks the sniffed format must raise")

    junk = write("junk.log", "completely unstructured\n")
    try:
        merge_files([junk])
    except ValueError:
        pass
    else:
        raise AssertionError("an unrecognizable first line must raise")


def test_empty_files_contribute_nothing():
    reset_fixtures()
    empty = write("empty.log", "")
    blanks = write("blanks.log", "\n   \n")
    one = write("one.log", "1700000000 worker ping\n")
    merged = merge_files([empty, blanks, one])
    assert [(x["source"], x["ts"]) for x in merged] == [(one, 1700000000.0)]


def main():
    tests = [
        test_sniff_format,
        test_parse_timestamp_exact,
        test_merge_across_formats,
        test_tie_break_follows_paths_order_then_lineno,
        test_skew_window_repairs_small_disorder,
        test_disorder_beyond_skew_is_rejected,
        test_blank_lines_skipped_but_numbering_is_physical,
        test_format_is_sniffed_per_file_and_enforced,
        test_empty_files_contribute_nothing,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
