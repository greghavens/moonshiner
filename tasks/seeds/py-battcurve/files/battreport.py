"""Discharge-curve report builder for the battery test rig.

Logs come off the rig as plain text: one sample per line,
``t_s current_mA voltage_mV`` (integers), '#' comments allowed. All the
integration is trapezoidal over the raw integer samples, so results are
exact and reproducible run to run.
"""
import numpy as np


def load_log(text):
    """Parse a rig log into a dict of int64 arrays keyed t_s / current_mA /
    voltage_mV. Timestamps must be strictly increasing and there must be at
    least two samples (you can't integrate a point)."""
    rows = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) != 3:
            raise ValueError(
                f"expected 3 columns (t_s current_mA voltage_mV), "
                f"got {len(parts)}: {stripped!r}")
        try:
            rows.append([int(p) for p in parts])
        except ValueError:
            raise ValueError(f"non-integer field in row: {stripped!r}") from None
    if len(rows) < 2:
        raise ValueError(f"need at least two samples, got {len(rows)}")
    data = np.array(rows, dtype=np.int64)
    t = data[:, 0]
    if np.any(np.diff(t) <= 0):
        raise ValueError("timestamps must be strictly increasing")
    return {"t_s": t, "current_mA": data[:, 1], "voltage_mV": data[:, 2]}


def charge_mAs(log):
    """Total delivered charge in mA*s (trapezoidal over the current trace)."""
    return float(np.trapezoid(log["current_mA"], log["t_s"]))


def energy_uWs(log):
    """Total delivered energy in uW*s: mA * mV integrated over seconds."""
    power_uW = log["current_mA"] * log["voltage_mV"]
    return float(np.trapezoid(power_uW, log["t_s"]))


def time_above_cutoff(log, cutoff_mV):
    """Seconds from the start of the log until the pack first reads strictly
    below cutoff_mV; a pack that never sags below the cutoff is credited
    with the full logged duration."""
    t = log["t_s"]
    below = log["voltage_mV"] < cutoff_mV
    first_below = int(np.argmax(below))
    return int(t[first_below] - t[0])


def pack_report(logs, cutoff_mV):
    """Fleet summary: per-pack charge/energy/holdup plus the healthiest pack
    (longest time above cutoff; ties go to the first name in sorted order)."""
    packs = {}
    for name in sorted(logs):
        log = logs[name]
        packs[name] = {
            "charge_mAs": charge_mAs(log),
            "energy_uWs": energy_uWs(log),
            "secs_above": time_above_cutoff(log, cutoff_mV),
        }
    healthiest = max(sorted(packs), key=lambda name: packs[name]["secs_above"])
    return {"packs": packs, "healthiest": healthiest}
