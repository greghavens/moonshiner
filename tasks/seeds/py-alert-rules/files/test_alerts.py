"""Behavior checks for the alert registry. Run: python3 test_alerts.py"""
from alerts import AlertRegistry, build_default_registry


def main():
    reg = AlertRegistry()
    reg.load_limits({"cpu_percent": 90.0, "queue_depth": 500.0, "error_rate": 2.0})
    assert reg.rule_count() == 3, f"expected 3 rules, got {reg.rule_count()}"

    # Only the breached metric fires.
    fired = reg.evaluate({"cpu_percent": 97.0, "queue_depth": 12.0, "error_rate": 0.1})
    assert fired == ["cpu_percent"], (
        f"cpu at 97 with limit 90 must fire exactly the cpu rule, got {fired!r}")

    fired = reg.evaluate({"cpu_percent": 40.0, "queue_depth": 900.0, "error_rate": 0.0})
    assert fired == ["queue_depth"], (
        f"queue at 900 with limit 500 must fire exactly the queue rule, got {fired!r}")

    # A breach of the last-loaded metric must not drag the others along.
    fired = reg.evaluate({"cpu_percent": 10.0, "queue_depth": 3.0, "error_rate": 9.5})
    assert fired == ["error_rate"], (
        f"only error_rate breached, got {fired!r}")

    # Nothing breached -> nothing fires; everything breached -> all fire in order.
    assert reg.evaluate({"cpu_percent": 50.0, "queue_depth": 100.0, "error_rate": 1.0}) == []
    fired = reg.evaluate({"cpu_percent": 95.0, "queue_depth": 800.0, "error_rate": 4.0})
    assert fired == ["cpu_percent", "queue_depth", "error_rate"], f"got {fired!r}"

    # Metrics absent from a sample count as 0, not as a breach.
    assert reg.evaluate({}) == [], "an empty sample must not fire anything"

    # Default registry behaves the same way.
    dreg = build_default_registry()
    fired = dreg.evaluate({"cpu_percent": 91.0})
    assert fired == ["cpu_percent"], f"default registry: got {fired!r}"

    print("all checks passed")


if __name__ == "__main__":
    main()
