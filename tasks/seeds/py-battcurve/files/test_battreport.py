"""Regression suite for the discharge-curve report builder.

All fixtures are integer samples, so trapezoidal results are exact floats
(sums of halves) — every assertion is equality, no tolerances.
"""
import numpy as np
import pytest

import battreport


SAG_LOG = """\
# pack B07 — sagged early
0 200 4000
30 180 3600
60 160 3400
90 140 3200
"""

HEALTHY_LOG = """\
# pack A03 — held voltage the whole run
0 150 4100
60 150 4050
120 140 4000
180 130 3900
"""

CUTOFF_MV = 3500


# ------------------------------------------------------------------- load_log

def test_load_log_parses_columns_as_int64():
    log = battreport.load_log(SAG_LOG)
    assert set(log) == {"t_s", "current_mA", "voltage_mV"}
    for key in log:
        assert log[key].dtype == np.dtype("int64")
    assert np.array_equal(log["t_s"], [0, 30, 60, 90])
    assert np.array_equal(log["current_mA"], [200, 180, 160, 140])
    assert np.array_equal(log["voltage_mV"], [4000, 3600, 3400, 3200])


def test_load_log_skips_comments_and_blank_lines():
    log = battreport.load_log("# rig 2\n\n0 10 4000\n\n5 12 3990\n")
    assert np.array_equal(log["t_s"], [0, 5])


def test_load_log_rejects_wrong_column_count():
    with pytest.raises(ValueError):
        battreport.load_log("0 10\n5 12\n")


def test_load_log_rejects_non_integer_fields():
    with pytest.raises(ValueError):
        battreport.load_log("0 10 4000\n5 1.5 3990\n")


def test_load_log_rejects_non_increasing_timestamps():
    with pytest.raises(ValueError):
        battreport.load_log("0 10 4000\n10 12 3990\n10 11 3980\n")


def test_load_log_rejects_single_sample():
    with pytest.raises(ValueError):
        battreport.load_log("0 10 4000\n")


# ------------------------------------------------------- charge & energy math

def test_charge_is_trapezoidal_not_rectangular():
    log = battreport.load_log("0 3 4000\n5 4 4000\n")
    # (3 + 4) / 2 * 5 — a left- or right-rectangle rule would give 15 or 20
    assert battreport.charge_mAs(log) == 17.5


def test_charge_mAs_exact_total():
    log = battreport.load_log("0 100 4200\n10 90 4100\n20 80 4000\n")
    assert battreport.charge_mAs(log) == 1800.0


def test_energy_uWs_exact_total():
    log = battreport.load_log("0 100 4200\n10 90 4100\n20 80 4000\n")
    assert battreport.energy_uWs(log) == 7390000.0


# ----------------------------------------------------------- time above cutoff

def test_time_above_cutoff_when_pack_sags():
    log = battreport.load_log(SAG_LOG)
    # first sample strictly below 3500 mV is at t=60
    assert battreport.time_above_cutoff(log, CUTOFF_MV) == 60


def test_time_above_cutoff_boundary_sample_not_below():
    log = battreport.load_log("0 10 3500\n30 10 3499\n")
    # 3500 is not strictly below the 3500 cutoff; 3499 at t=30 is
    assert battreport.time_above_cutoff(log, CUTOFF_MV) == 30


def test_time_above_cutoff_first_sample_already_below():
    log = battreport.load_log("0 10 3400\n30 10 3300\n")
    assert battreport.time_above_cutoff(log, CUTOFF_MV) == 0


def test_time_above_cutoff_pack_never_below_gets_full_duration():
    log = battreport.load_log(HEALTHY_LOG)
    assert battreport.time_above_cutoff(log, CUTOFF_MV) == 180


def test_time_above_cutoff_returns_plain_int():
    log = battreport.load_log(SAG_LOG)
    assert type(battreport.time_above_cutoff(log, CUTOFF_MV)) is int


# ---------------------------------------------------------------- pack_report

def test_pack_report_per_pack_numbers():
    logs = {"B07": battreport.load_log(SAG_LOG),
            "A03": battreport.load_log(HEALTHY_LOG)}
    report = battreport.pack_report(logs, CUTOFF_MV)
    assert report["packs"]["B07"]["charge_mAs"] == 15300.0
    assert report["packs"]["A03"]["charge_mAs"] == 25800.0
    assert report["packs"]["A03"]["energy_uWs"] == 103710000.0
    assert report["packs"]["B07"]["secs_above"] == 60


def test_pack_report_healthiest_is_pack_that_held_voltage():
    logs = {"B07": battreport.load_log(SAG_LOG),
            "A03": battreport.load_log(HEALTHY_LOG)}
    report = battreport.pack_report(logs, CUTOFF_MV)
    assert report["packs"]["A03"]["secs_above"] == 180
    assert report["healthiest"] == "A03"
