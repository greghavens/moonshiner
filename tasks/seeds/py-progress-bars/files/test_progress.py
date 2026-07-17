"""Acceptance checks for progress.py. Run: python3 test_progress.py"""
import progress
from progress import ProgressBar


class Clock:
    """Hand-cranked clock: call it to read .now, bump .now to pass time."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# ---------------------------------------------------------------- existing

def test_renders_half_done_bar():
    bar = ProgressBar(10, width=10)
    bar.advance(5)
    assert bar.render() == "[#####-----]  50% (5/10)", bar.render()
    assert not bar.done


def test_label_prefixes_the_line():
    bar = ProgressBar(4, label="fetch", width=4)
    bar.advance(1)
    assert bar.render() == "fetch [#---]  25% (1/4)", bar.render()


def test_progress_clamps_at_total():
    bar = ProgressBar(10, width=10)
    bar.advance(7)
    bar.advance(7)
    assert bar.current == 10, bar.current
    assert bar.done
    assert bar.render() == "[##########] 100% (10/10)", bar.render()


def test_width_is_injectable_and_percent_floors():
    bar = ProgressBar(3, width=6)
    bar.advance(2)
    assert bar.render() == "[####--]  66% (2/3)", bar.render()


def test_zero_total_job_is_complete():
    bar = ProgressBar(0, width=5)
    assert bar.done
    assert bar.render() == "[#####] 100% (0/0)", bar.render()


def test_rejects_bad_construction():
    for bad in (lambda: ProgressBar(-1), lambda: ProgressBar(5, width=0)):
        try:
            bad()
            assert False, "constructor accepted invalid arguments"
        except ValueError:
            pass


# ----------------------------------------------------------------- feature

def test_eta_is_unknown_before_any_sample():
    clk = Clock()
    bar = ProgressBar(100, width=10, clock=clk)
    assert bar.eta() is None
    assert bar.render(show_eta=True) == "[----------]   0% (0/100)", \
        bar.render(show_eta=True)


def test_eta_from_first_sample():
    clk = Clock()
    bar = ProgressBar(100, width=10, clock=clk)
    clk.now = 10.0
    bar.advance(10)
    assert bar.eta() == 90.0, bar.eta()
    line = bar.render(show_eta=True)
    assert line == "[#---------]  10% (10/100) ETA 1:30", line


def test_eta_smooths_with_moving_average():
    clk = Clock()
    bar = ProgressBar(100, clock=clk)
    clk.now = 10.0
    bar.advance(10)          # sample 1.0/s -> rate 1.0
    clk.now = 20.0
    bar.advance(30)          # sample 3.0/s -> rate 0.5*3.0 + 0.5*1.0 = 2.0
    assert bar.eta() == 30.0, bar.eta()
    assert bar.render(show_eta=True).endswith("ETA 0:30"), \
        bar.render(show_eta=True)


def test_zero_elapsed_samples_are_ignored():
    clk = Clock()
    bar = ProgressBar(100, clock=clk)
    bar.advance(50)          # no time has passed: not a usable rate sample
    assert bar.eta() is None
    clk.now = 5.0
    bar.advance(10)          # 10 units / 5s -> rate 2.0, 40 left -> 20s
    assert bar.eta() == 20.0, bar.eta()


def test_finished_bar_has_zero_eta_and_no_suffix():
    clk = Clock()
    bar = ProgressBar(10, width=4, clock=clk)
    clk.now = 5.0
    bar.advance(10)
    assert bar.eta() == 0.0, bar.eta()
    assert bar.render(show_eta=True) == "[####] 100% (10/10)", \
        bar.render(show_eta=True)


def test_plain_render_never_shows_eta():
    clk = Clock()
    bar = ProgressBar(100, width=10, clock=clk)
    clk.now = 10.0
    bar.advance(10)
    assert bar.render() == "[#---------]  10% (10/100)", bar.render()
    assert "ETA" not in bar.render()


def test_multibar_renders_in_add_order_with_aligned_labels():
    mb = progress.MultiBar(width=4)
    a = mb.add("fetch", 4)
    b = mb.add("transcode", 2)
    b.advance(1)             # updated first, must still render second
    a.advance(1)
    lines = mb.render()
    assert lines == ["fetch     [#---]  25% (1/4)",
                     "transcode [##--]  50% (1/2)"], lines


def test_multibar_rejects_duplicate_labels():
    mb = progress.MultiBar()
    mb.add("upload", 3)
    try:
        mb.add("upload", 9)
        assert False, "duplicate label was accepted"
    except ValueError:
        pass


def test_multibar_render_can_show_etas():
    clk = Clock()
    mb = progress.MultiBar(width=4, clock=clk)
    a = mb.add("up", 8)
    mb.add("down", 4)
    clk.now = 4.0
    a.advance(2)             # 0.5/s, 6 left -> 12s
    lines = mb.render(show_eta=True)
    assert lines == ["up   [#---]  25% (2/8) ETA 0:12",
                     "down [----]   0% (0/4)"], lines


def test_empty_multibar_renders_nothing():
    assert progress.MultiBar().render() == []


def test_summary_joins_jobs_and_overall_percent():
    clk = Clock()            # frozen: no elapsed time, so no ETA appears
    mb = progress.MultiBar(clock=clk)
    mb.add("noop", 0)
    work = mb.add("work", 10)
    work.advance(5)
    assert mb.summary() == "noop 100% | work 50% >> 50%", mb.summary()


def test_summary_appends_largest_remaining_eta():
    clk = Clock()
    mb = progress.MultiBar(clock=clk)
    fetch = mb.add("fetch", 100)
    index = mb.add("index", 50)
    clk.now = 10.0
    fetch.advance(10)        # 1.0/s, 90 left -> 90s
    index.advance(25)        # 2.5/s, 25 left -> 10s
    assert mb.summary() == "fetch 10% | index 50% >> 23% ETA 1:30", \
        mb.summary()


def test_summary_ignores_finished_bars_for_eta():
    clk = Clock()
    mb = progress.MultiBar(clock=clk)
    copy = mb.add("copy", 4)
    scan = mb.add("scan", 100)
    clk.now = 2.0
    copy.advance(4)          # done: must not contribute an ETA
    scan.advance(10)         # 5.0/s, 90 left -> 18s
    assert mb.summary() == "copy 100% | scan 10% >> 13% ETA 0:18", \
        mb.summary()

    clk2 = Clock()
    mb2 = progress.MultiBar(clock=clk2)
    one = mb2.add("one", 2)
    clk2.now = 1.0
    one.advance(2)
    assert mb2.summary() == "one 100% >> 100%", mb2.summary()


EXISTING = [
    test_renders_half_done_bar,
    test_label_prefixes_the_line,
    test_progress_clamps_at_total,
    test_width_is_injectable_and_percent_floors,
    test_zero_total_job_is_complete,
    test_rejects_bad_construction,
]

FEATURE = [
    test_eta_is_unknown_before_any_sample,
    test_eta_from_first_sample,
    test_eta_smooths_with_moving_average,
    test_zero_elapsed_samples_are_ignored,
    test_finished_bar_has_zero_eta_and_no_suffix,
    test_plain_render_never_shows_eta,
    test_multibar_renders_in_add_order_with_aligned_labels,
    test_multibar_rejects_duplicate_labels,
    test_multibar_render_can_show_etas,
    test_empty_multibar_renders_nothing,
    test_summary_joins_jobs_and_overall_percent,
    test_summary_appends_largest_remaining_eta,
    test_summary_ignores_finished_bars_for_eta,
]


def main():
    failures = 0
    for t in EXISTING + FEATURE:
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
    print("\nall %d checks passed" % len(EXISTING + FEATURE))


if __name__ == "__main__":
    main()
