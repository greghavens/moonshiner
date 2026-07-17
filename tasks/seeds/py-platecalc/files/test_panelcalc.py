"""Acceptance tests for the photovoltaic panel math module (panelcalc.py).

Readings are integers end to end, so every expected value is exact — no
tolerances. Cell names follow panel convention: row letter (A..) plus
1-based column number, row-major order.
"""
import numpy as np
import pytest

import panelcalc


I64 = np.dtype("int64")

PANEL_TEXT = """\
# panel 7 — day 2 sweep
# tester: FLX-800, gain 35

95 12 12 12 12 12 12 12 12 12 12 10
12  7 12 12 12 12 12 12 12 12 12 10
12 12 12 12 12 12 60 12 12 12 12 10
12 12 12 12 12 12 12 12 12 12 12 10
12 12 12 12 12 12 12 12 12 12 12 10
12 12 12 12 12 12 12 12 12 12 12 10
12 12 12 12 12 12 12 12 12 12 12 10
50 12 12 12 12 12 12 12 12 12 12 10
"""


# ---------------------------------------------------------------- parse_panel

def test_parse_panel_shape_and_dtype():
    panel = panelcalc.parse_panel(PANEL_TEXT)
    assert isinstance(panel, np.ndarray)
    assert panel.shape == (8, 12)
    assert panel.dtype == I64


def test_parse_panel_values():
    panel = panelcalc.parse_panel(PANEL_TEXT)
    assert panel[0, 0] == 95
    assert panel[1, 1] == 7
    assert panel[2, 6] == 60
    assert panel[7, 0] == 50
    assert panel[7, 11] == 10
    # 4 standout cells + 84 background cells of 12 + 8 dark-reference cells of 10
    assert panel.sum() == (95 + 7 + 60 + 50) + 84 * 12 + 8 * 10


def test_parse_panel_skips_comments_and_empty_lines():
    rows = ["1 2 3 4 5 6 7 8 9 10 11 12"] * 8
    text = "# header\n\n" + "\n\n".join(rows) + "\n# trailing note\n"
    panel = panelcalc.parse_panel(text)
    assert panel.shape == (8, 12)
    assert np.array_equal(panel[3], np.arange(1, 13))


def test_parse_panel_rejects_wrong_row_count():
    rows = ["1 2 3 4 5 6 7 8 9 10 11 12"] * 7
    with pytest.raises(ValueError):
        panelcalc.parse_panel("\n".join(rows))


def test_parse_panel_rejects_wrong_column_count():
    rows = ["1 2 3 4 5 6 7 8 9 10 11 12"] * 7 + ["1 2 3"]
    with pytest.raises(ValueError):
        panelcalc.parse_panel("\n".join(rows))


def test_parse_panel_rejects_non_integer_tokens():
    rows = ["1 2 3 4 5 6 7 8 9 10 11 12"] * 7
    rows.append("1 2 3 4 5 6 7 8 9 10 11 12.5")
    with pytest.raises(ValueError):
        panelcalc.parse_panel("\n".join(rows))


# ----------------------------------------------------------- baseline_correct

def test_baseline_correct_subtracts_row_dark():
    grid = np.array([[5, 1, 9],
                     [7, 3, 2]])
    out = panelcalc.baseline_correct(grid, 1)
    assert out.dtype == I64
    assert np.array_equal(out, [[4, 0, 8],
                                [4, 0, 0]])


def test_baseline_correct_clamps_at_zero():
    grid = np.array([[2, 10]])
    out = panelcalc.baseline_correct(grid, 1)
    assert np.array_equal(out, [[0, 0]])


def test_baseline_correct_does_not_modify_input():
    grid = np.array([[5, 1], [7, 3]])
    before = grid.copy()
    panelcalc.baseline_correct(grid, 0)
    assert np.array_equal(grid, before)


@pytest.mark.parametrize("col", [-1, 3, 12])
def test_baseline_correct_rejects_out_of_range_column(col):
    grid = np.array([[5, 1, 9], [7, 3, 2]])
    with pytest.raises(ValueError):
        panelcalc.baseline_correct(grid, col)


# --------------------------------------------------------------- panel_summary

def test_panel_summary_totals_and_peak():
    grid = np.array([[1, 9],
                     [4, 9]])
    summary = panelcalc.panel_summary(grid)
    assert summary["total"] == 23
    assert summary["peak"] == 9
    assert summary["peak_cell"] == "A2"  # first peak in row-major order


def test_panel_summary_values_are_plain_python_ints():
    grid = np.array([[3, 5], [1, 2]])
    summary = panelcalc.panel_summary(grid)
    assert type(summary["total"]) is int
    assert type(summary["peak"]) is int


def test_panel_summary_peak_in_later_row():
    grid = np.array([[1, 2, 3],
                     [4, 40, 5]])
    summary = panelcalc.panel_summary(grid)
    assert summary["peak"] == 40
    assert summary["peak_cell"] == "B2"


# -------------------------------------------------------------------- flagged

def test_flagged_strictly_above_threshold_row_major():
    grid = np.array([[10, 2],
                     [3, 10]])
    assert panelcalc.flagged(grid, 3) == ["A1", "B2"]


def test_flagged_multiple_in_one_row_keep_column_order():
    grid = np.array([[9, 1, 8],
                     [1, 7, 1]])
    assert panelcalc.flagged(grid, 5) == ["A1", "A3", "B2"]


def test_flagged_none_above_threshold():
    grid = np.array([[1, 2], [3, 4]])
    assert panelcalc.flagged(grid, 4) == []


def test_flagged_equal_to_threshold_excluded():
    grid = np.array([[6, 6]])
    assert panelcalc.flagged(grid, 6) == []


# ---------------------------------------------------------------- integration

def test_full_read_correct_and_call_pipeline():
    panel = panelcalc.parse_panel(PANEL_TEXT)
    corrected = panelcalc.baseline_correct(panel, 11)
    summary = panelcalc.panel_summary(corrected)
    assert summary["peak"] == 85
    assert summary["peak_cell"] == "A1"
    assert summary["total"] == 343
    assert panelcalc.flagged(corrected, 30) == ["A1", "C7", "H1"]
