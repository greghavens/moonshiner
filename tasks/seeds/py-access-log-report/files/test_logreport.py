"""Behavior checks for the log report. Run: python3 test_logreport.py"""
from logreport import summarize

LOG = [
    "# window 2026-06-30T04:00",
    "10.0.0.5\tGET\t/\t200\t42.0",
    "10.0.0.5\tGET\t/search\t200\t812.5",
    "10.0.0.9\tPOST\t/api/orders\t500\t120.0",
    "",
    "10.0.0.7\tGET\t/health\t200\t3.1",
    "not a log line at all",
    "10.0.0.9\tPOST\t/api/orders\t502\t950.0",
    "10.0.0.2\tGET\t/search\t404\t77.0",
]


def main():
    report = summarize(LOG)

    assert report["total"] == 6, f"6 well-formed lines, got total={report['total']!r}"

    want_status = {200: 3, 500: 1, 502: 1, 404: 1}
    assert report["by_status"] == want_status, (
        f"status breakdown must cover the same lines the total counted, "
        f"got {report['by_status']!r}")

    assert report["slow_paths"] == ["/api/orders", "/search"], (
        f"requests at/over 500ms are slow, got {report['slow_paths']!r}")

    want_rate = 2 / 6
    assert abs(report["error_rate"] - want_rate) < 1e-9, (
        f"2 of 6 requests were 5xx, got error_rate={report['error_rate']!r}")

    # Edge: an empty window reports cleanly.
    empty = summarize([])
    assert empty == {"total": 0, "by_status": {}, "slow_paths": [], "error_rate": 0.0}, (
        f"empty window report wrong: {empty!r}")

    print("all checks passed")


if __name__ == "__main__":
    main()
