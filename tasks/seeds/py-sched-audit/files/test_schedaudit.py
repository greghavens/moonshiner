"""Acceptance tests for schedaudit. Run: python3 test_schedaudit.py"""
from schedaudit import expand, find_conflicts, suggest_slots

WS = "2026-04-06T00:00"
WE = "2026-04-07T00:00"


def job(jid, resource, start, duration_min, every=None, unit=None):
    j = {"id": jid, "resource": resource, "start": start,
         "duration_min": duration_min, "repeat": None}
    if every is not None:
        j["repeat"] = {"every": every, "unit": unit}
    return j


def raises_value_error(fn, label):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError(f"{label} should raise ValueError")


def test_expand_single_occurrence_window_edges():
    j = job("backup", "db", "2026-04-06T01:00", 45)
    assert expand(j, WS, WE) == [("2026-04-06T01:00", "2026-04-06T01:45")]
    # spills into the window from before: included
    j = job("early", "db", "2026-04-05T23:30", 60)
    assert expand(j, WS, WE) == [("2026-04-05T23:30", "2026-04-06T00:30")]
    # ends exactly at window start: intervals are half-open, excluded
    j = job("done", "db", "2026-04-05T23:00", 60)
    assert expand(j, WS, WE) == []
    # starts exactly at window end: excluded
    j = job("late", "db", "2026-04-07T00:00", 30)
    assert expand(j, WS, WE) == []


def test_expand_recurrences():
    j = job("etl", "db", "2026-04-06T00:30", 20, every=6, unit="hours")
    assert expand(j, WS, WE) == [
        ("2026-04-06T00:30", "2026-04-06T00:50"),
        ("2026-04-06T06:30", "2026-04-06T06:50"),
        ("2026-04-06T12:30", "2026-04-06T12:50"),
        ("2026-04-06T18:30", "2026-04-06T18:50"),
    ]
    j = job("sync", "db", "2026-04-06T00:00", 30, every=90, unit="minutes")
    assert expand(j, "2026-04-06T00:00", "2026-04-06T06:00") == [
        ("2026-04-06T00:00", "2026-04-06T00:30"),
        ("2026-04-06T01:30", "2026-04-06T02:00"),
        ("2026-04-06T03:00", "2026-04-06T03:30"),
        ("2026-04-06T04:30", "2026-04-06T05:00"),
    ]
    # a daily job that started long before the window still expands correctly
    j = job("report", "db", "2026-04-01T09:00", 60, every=1, unit="days")
    assert expand(j, WS, "2026-04-08T00:00") == [
        ("2026-04-06T09:00", "2026-04-06T10:00"),
        ("2026-04-07T09:00", "2026-04-07T10:00"),
    ]


def test_conflicts_same_resource_only_half_open():
    jobs = [
        job("nightly-backup", "db-primary", "2026-04-06T01:00", 90),
        job("schema-migrate", "db-primary", "2026-04-06T02:00", 60),
        # touching intervals on cache: not a conflict
        job("warm-a", "cache", "2026-04-06T01:00", 60),
        job("warm-b", "cache", "2026-04-06T02:00", 60),
        # same times as the db-primary pair, but a different resource
        job("replica-backup", "db-replica", "2026-04-06T01:00", 90),
    ]
    assert find_conflicts(jobs, WS, WE) == [{
        "resource": "db-primary",
        "jobs": ["nightly-backup", "schema-migrate"],
        "start": "2026-04-06T02:00",
        "end": "2026-04-06T02:30",
    }]


def test_job_overlapping_its_own_next_run():
    j = job("poller", "gateway", "2026-04-06T00:00", 40, every=30, unit="minutes")
    got = find_conflicts([j], "2026-04-06T00:00", "2026-04-06T02:00")
    assert got == [
        {"resource": "gateway", "jobs": ["poller", "poller"],
         "start": "2026-04-06T00:30", "end": "2026-04-06T00:40"},
        {"resource": "gateway", "jobs": ["poller", "poller"],
         "start": "2026-04-06T01:00", "end": "2026-04-06T01:10"},
        {"resource": "gateway", "jobs": ["poller", "poller"],
         "start": "2026-04-06T01:30", "end": "2026-04-06T01:40"},
    ], got


def test_conflict_report_ordering():
    jobs = [
        job("build", "a", "2026-04-06T00:30", 60),
        job("apply", "a", "2026-04-06T00:00", 60),
        job("clean", "a", "2026-04-06T00:30", 45),
        job("dump", "b", "2026-04-06T00:30", 30),
        job("emit", "b", "2026-04-06T00:30", 30),
    ]
    got = find_conflicts(jobs, WS, "2026-04-06T04:00")
    assert got == [
        {"resource": "a", "jobs": ["apply", "build"],
         "start": "2026-04-06T00:30", "end": "2026-04-06T01:00"},
        {"resource": "a", "jobs": ["apply", "clean"],
         "start": "2026-04-06T00:30", "end": "2026-04-06T01:00"},
        {"resource": "b", "jobs": ["dump", "emit"],
         "start": "2026-04-06T00:30", "end": "2026-04-06T01:00"},
        {"resource": "a", "jobs": ["build", "clean"],
         "start": "2026-04-06T00:30", "end": "2026-04-06T01:15"},
    ], got


def test_validation():
    ok = job("a", "r", "2026-04-06T01:00", 30)
    raises_value_error(lambda: find_conflicts([ok, job("a", "r", "2026-04-06T02:00", 30)], WS, WE),
                       "duplicate job id")
    raises_value_error(lambda: expand(job("b", "r", "2026-04-06T01:00", 0), WS, WE),
                       "zero duration")
    raises_value_error(lambda: expand(job("b", "r", "2026-04-06T01:00", -5), WS, WE),
                       "negative duration")
    raises_value_error(lambda: expand(job("b", "r", "2026-04-06 01:00", 30), WS, WE),
                       "malformed start")
    raises_value_error(lambda: expand(job("b", "r", "2026-04-06T01:00", 30,
                                          every=1, unit="weeks"), WS, WE),
                       "unsupported unit")
    raises_value_error(lambda: expand(job("b", "r", "2026-04-06T01:00", 30,
                                          every=0, unit="hours"), WS, WE),
                       "zero repeat")
    raises_value_error(lambda: expand(ok, WE, WS), "reversed window")
    raises_value_error(lambda: expand(ok, WS, WS), "empty window")
    raises_value_error(lambda: expand({"id": "x", "resource": "r", "start": WS}, WS, WE),
                       "missing duration")


def test_suggest_slots_earliest_first():
    jobs = [
        job("backup", "db-primary", "2026-04-06T01:00", 90),
        job("etl", "db-primary", "2026-04-06T00:00", 30, every=2, unit="hours"),
        job("noise", "cache", "2026-04-06T00:00", 360),
    ]
    got = suggest_slots(jobs, "db-primary", 60, WS, "2026-04-06T06:00",
                        granularity_min=30, limit=3)
    assert got == ["2026-04-06T02:30", "2026-04-06T03:00", "2026-04-06T04:30"], got
    assert suggest_slots(jobs, "db-primary", 60, WS, "2026-04-06T06:00",
                         granularity_min=30, limit=1) == ["2026-04-06T02:30"]
    # jobs on other resources do not block the slot search
    assert suggest_slots(jobs, "idle-pool", 60, WS, "2026-04-06T06:00",
                         granularity_min=30, limit=2) == [
        "2026-04-06T00:00", "2026-04-06T00:30"]


def test_suggest_slot_must_fit_inside_window():
    jobs = [job("busy", "r1", "2026-04-06T00:00", 60)]
    # the only candidate that would fit (00:00) is occupied
    assert suggest_slots(jobs, "r1", 120, WS, "2026-04-06T02:00",
                         granularity_min=30) == []
    # candidates step from window_start, not from the top of the hour
    assert suggest_slots([], "r1", 20, "2026-04-06T00:10", "2026-04-06T01:00",
                         granularity_min=15, limit=2) == [
        "2026-04-06T00:10", "2026-04-06T00:25"]


def test_suggest_slots_validation():
    raises_value_error(lambda: suggest_slots([], "r", 0, WS, WE), "zero duration")
    raises_value_error(lambda: suggest_slots([], "r", 30, WS, WE, granularity_min=0),
                       "zero granularity")
    raises_value_error(
        lambda: suggest_slots([job("a", "r", WS, 5), job("a", "r", WS, 5)], "r", 30, WS, WE),
        "duplicate job id")


def main():
    tests = [
        test_expand_single_occurrence_window_edges,
        test_expand_recurrences,
        test_conflicts_same_resource_only_half_open,
        test_job_overlapping_its_own_next_run,
        test_conflict_report_ordering,
        test_validation,
        test_suggest_slots_earliest_first,
        test_suggest_slot_must_fit_inside_window,
        test_suggest_slots_validation,
    ]
    for test in tests:
        test()
        print(f"ok - {test.__name__}")
    print(f"all {len(tests)} test groups passed")


if __name__ == "__main__":
    main()
