"""Threshold alerting for the metrics pipeline.

Limits are loaded once (metric name -> upper bound); every incoming sample
batch is then evaluated against all rules and the names of the metrics that
breached come back, in the order the rules were installed.
"""

DEFAULT_LIMITS = {
    "cpu_percent": 90.0,
    "queue_depth": 500.0,
    "error_rate": 2.0,
}


class AlertRegistry:
    def __init__(self):
        self._rules = []  # list of (metric_name, check_fn)

    def load_limits(self, limits):
        """Install one rule per metric: it fires when the sample value for
        that metric exceeds that metric's limit."""
        for metric, limit in limits.items():
            check = lambda sample: sample.get(metric, 0.0) > limit
            self._rules.append((metric, check))

    def rule_count(self):
        return len(self._rules)

    def clear(self):
        self._rules = []

    def evaluate(self, sample):
        """Return the metric names whose rule fired for *sample*."""
        fired = []
        for metric, check in self._rules:
            if check(sample):
                fired.append(metric)
        return fired


def build_default_registry():
    registry = AlertRegistry()
    registry.load_limits(DEFAULT_LIMITS)
    return registry
