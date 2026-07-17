"""Single-line text progress bar for long-running CLI jobs.

A ProgressBar tracks one counted job and renders the classic
``[#####-----]  50% (5/10)`` line.  The bar-body width is injectable so
callers can size it to their terminal, and the percentage is floored so a
job never reads 100% until it truly is finished.
"""


def format_bar(current, total, width):
    """The bar body: ``width`` cells, '#' for done, '-' for remaining."""
    if total <= 0:
        filled = width
    else:
        filled = int(width * min(current, total) / total)
    return "#" * filled + "-" * (width - filled)


def percent_done(current, total):
    """Whole-number percent complete, floored; an empty job counts as done."""
    if total <= 0:
        return 100
    return min(100, current * 100 // total)


class ProgressBar:
    """Progress of one counted job."""

    def __init__(self, total, label="", width=24):
        if total < 0:
            raise ValueError("total must be >= 0")
        if width < 1:
            raise ValueError("width must be >= 1")
        self.total = total
        self.label = label
        self.width = width
        self.current = 0

    def advance(self, n=1):
        """Record ``n`` more completed units; progress never passes total."""
        if n > 0:
            self.current = min(self.total, self.current + n)

    @property
    def done(self):
        return self.current >= self.total

    def render(self):
        """The full line, e.g. ``fetch [##--]  50% (2/4)``."""
        line = "[%s] %3d%% (%d/%d)" % (
            format_bar(self.current, self.total, self.width),
            percent_done(self.current, self.total),
            self.current,
            self.total,
        )
        if self.label:
            line = "%s %s" % (self.label, line)
        return line
