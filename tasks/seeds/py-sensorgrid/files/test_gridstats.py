"""Acceptance tests for the greenhouse grid-stats module (gridstats.py).

All fixtures are small integer arrays so every expected value is exact —
no tolerances anywhere. Dashboards downstream do integer math, so the
int64 dtype of each result is part of the contract.
"""
import numpy as np
import pytest

import gridstats


I64 = np.dtype("int64")


# ---------------------------------------------------------------- window_sums

def test_window_sums_basic():
    out = gridstats.window_sums([3, 1, 4, 1, 5, 9, 2, 6], 3)
    assert isinstance(out, np.ndarray)
    assert out.dtype == I64
    assert np.array_equal(out, [8, 6, 10, 15, 16, 17])


def test_window_sums_width_one_is_identity():
    out = gridstats.window_sums(np.array([7, 0, -3, 12]), 1)
    assert out.dtype == I64
    assert np.array_equal(out, [7, 0, -3, 12])


def test_window_sums_full_width_single_total():
    out = gridstats.window_sums([3, 1, 4, 1, 5, 9, 2, 6], 8)
    assert out.shape == (1,)
    assert out[0] == 31


def test_window_sums_handles_negative_readings():
    out = gridstats.window_sums([-2, 5, -1], 2)
    assert np.array_equal(out, [3, 4])


@pytest.mark.parametrize("width", [0, -3, 9])
def test_window_sums_rejects_bad_width(width):
    with pytest.raises(ValueError):
        gridstats.window_sums([3, 1, 4, 1, 5, 9, 2, 6], width)


# --------------------------------------------------------------- window_peaks

def test_window_peaks_basic():
    out = gridstats.window_peaks([2, 9, 3, 5, 4], 2)
    assert out.dtype == I64
    assert np.array_equal(out, [9, 9, 5, 5])


def test_window_peaks_single_reading():
    out = gridstats.window_peaks([7], 1)
    assert np.array_equal(out, [7])


def test_window_peaks_all_negative():
    out = gridstats.window_peaks([-5, -2, -9], 2)
    assert np.array_equal(out, [-2, -2])


@pytest.mark.parametrize("width", [0, 6])
def test_window_peaks_rejects_bad_width(width):
    with pytest.raises(ValueError):
        gridstats.window_peaks([2, 9, 3, 5, 4], width)


# ------------------------------------------------------------------ calibrate

def test_calibrate_offsets_then_gains_per_sensor_row():
    raw = np.array([[11, 12, 13],
                    [25, 20, 30]])
    out = gridstats.calibrate(raw, offsets=[10, 20], gains=[2, 3])
    assert out.dtype == I64
    assert np.array_equal(out, [[2, 4, 6],
                                [15, 0, 30]])


def test_calibrate_square_grid_applies_vectors_along_rows():
    # sensors x minutes is 2x2 here, so applying the vectors along the
    # wrong axis would still broadcast — the expected values pin the axis.
    raw = np.array([[1, 2],
                    [3, 4]])
    out = gridstats.calibrate(raw, offsets=[1, 2], gains=[1, 1])
    assert np.array_equal(out, [[0, 1],
                                [1, 2]])


def test_calibrate_does_not_modify_raw_input():
    raw = np.array([[5, 6], [7, 8]])
    before = raw.copy()
    gridstats.calibrate(raw, offsets=[1, 1], gains=[10, 10])
    assert np.array_equal(raw, before)


def test_calibrate_accepts_lists():
    out = gridstats.calibrate([[4, 6]], offsets=[4], gains=[3])
    assert out.dtype == I64
    assert np.array_equal(out, [[0, 6]])


def test_calibrate_rejects_wrong_length_vectors():
    raw = np.array([[1, 2, 3], [4, 5, 6]])
    with pytest.raises(ValueError):
        gridstats.calibrate(raw, offsets=[1, 2, 3], gains=[1, 2])
    with pytest.raises(ValueError):
        gridstats.calibrate(raw, offsets=[1, 2], gains=[1])


def test_calibrate_rejects_non_2d_raw():
    with pytest.raises(ValueError):
        gridstats.calibrate([1, 2, 3], offsets=[1], gains=[1])


def test_calibrate_rejects_non_1d_vectors():
    raw = np.array([[1, 2], [3, 4]])
    with pytest.raises(ValueError):
        gridstats.calibrate(raw, offsets=[[1], [2]], gains=[1, 2])


# ------------------------------------------------------------ downsample_sums

def test_downsample_sums_blocks_of_two():
    out = gridstats.downsample_sums([1, 2, 3, 4, 5, 6], 2)
    assert out.dtype == I64
    assert np.array_equal(out, [3, 7, 11])


def test_downsample_sums_whole_series_one_block():
    out = gridstats.downsample_sums([4, 4, 4], 3)
    assert np.array_equal(out, [12])


def test_downsample_sums_k_one_is_identity():
    out = gridstats.downsample_sums([9, -1, 0], 1)
    assert np.array_equal(out, [9, -1, 0])


def test_downsample_sums_empty_series():
    out = gridstats.downsample_sums([], 1)
    assert out.shape == (0,)
    assert out.dtype == I64


def test_downsample_sums_rejects_partial_blocks():
    with pytest.raises(ValueError):
        gridstats.downsample_sums([1, 2, 3], 2)


def test_downsample_sums_rejects_bad_k():
    with pytest.raises(ValueError):
        gridstats.downsample_sums([1, 2, 3], 0)


# -------------------------------------------------------------- above_streaks

def test_above_streaks_per_row_longest_run():
    grid = [[1, 5, 5, 5, 1],
            [6, 6, 1, 6, 6]]
    out = gridstats.above_streaks(grid, 4)
    assert out.dtype == I64
    assert np.array_equal(out, [3, 2])


def test_above_streaks_run_touching_row_end_counts():
    out = gridstats.above_streaks([[1, 7, 7]], 4)
    assert np.array_equal(out, [2])


def test_above_streaks_run_at_row_start_counts():
    out = gridstats.above_streaks([[7, 7, 1]], 4)
    assert np.array_equal(out, [2])


def test_above_streaks_strictly_above():
    out = gridstats.above_streaks([[4, 4, 4]], 4)
    assert np.array_equal(out, [0])


def test_above_streaks_row_never_above_is_zero():
    grid = [[9, 9, 9],
            [1, 2, 3]]
    out = gridstats.above_streaks(grid, 5)
    assert np.array_equal(out, [3, 0])


def test_above_streaks_rejects_non_2d():
    with pytest.raises(ValueError):
        gridstats.above_streaks([1, 2, 3], 2)
