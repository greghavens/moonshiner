"""Contract tests for the usage report module — protected file."""
from pathlib import Path

import pandas as pd
import pytest

from report import load_usage, monthly_matrix, team_totals, write_report

FIXTURE = Path(__file__).parent / "usage.csv"

EXPECTED_REPORT = (
    "month,bandwidth,compute,storage\n"
    "2025-01,0.00,40.00,6.00\n"
    "2025-02,6.00,30.00,3.00\n"
    "2025-03,3.00,20.00,12.00\n"
)


@pytest.fixture()
def df():
    return load_usage(FIXTURE)


def test_load_columns_and_row_count(df):
    assert list(df.columns) == ["date", "team", "resource", "hours", "rate", "cost"]
    assert len(df) == 10


def test_load_dtypes(df):
    assert str(df["date"].dtype) == "datetime64[ns]"
    assert str(df["team"].dtype) == "category"
    assert str(df["resource"].dtype) == "category"
    assert str(df["hours"].dtype) == "int64"
    assert str(df["rate"].dtype) == "float64"
    assert str(df["cost"].dtype) == "float64"


def test_load_dates_are_real_timestamps(df):
    assert df["date"].iloc[0] == pd.Timestamp("2025-01-06")
    assert sorted(df["date"].dt.month.unique().tolist()) == [1, 2, 3]


def test_load_cost_is_hours_times_rate(df):
    assert df["cost"].iloc[0] == pytest.approx(25.0)   # 10 * 2.50
    assert df["cost"].iloc[5] == pytest.approx(6.0)    # 20 * 0.30
    assert df["cost"].sum() == pytest.approx(120.0)


def test_team_totals_frame(df):
    expected = pd.DataFrame({
        "team": ["mobile", "platform", "search"],
        "hours": [41, 40, 13],
        "cost": [30.5, 64.0, 25.5],
        "entries": [3, 4, 3],
    })
    pd.testing.assert_frame_equal(team_totals(df), expected)


def test_monthly_matrix_index_and_columns(df):
    m = monthly_matrix(df)
    assert m.index.name == "month"
    assert list(m.index) == ["2025-01", "2025-02", "2025-03"]
    assert list(m.columns) == ["bandwidth", "compute", "storage"]


def test_monthly_matrix_values(df):
    m = monthly_matrix(df)
    assert m.loc["2025-01", "compute"] == pytest.approx(40.0)
    assert m.loc["2025-02", "bandwidth"] == pytest.approx(6.0)
    assert m.loc["2025-03", "storage"] == pytest.approx(12.0)
    assert m.to_numpy().sum() == pytest.approx(120.0)


def test_monthly_matrix_missing_combinations_are_zero_not_nan(df):
    m = monthly_matrix(df)
    # no bandwidth usage at all in January
    assert m.loc["2025-01", "bandwidth"] == 0.0
    assert not m.isna().to_numpy().any()


def test_helpers_do_not_mutate_input(df):
    snapshot = df.copy(deep=True)
    team_totals(df)
    monthly_matrix(df)
    pd.testing.assert_frame_equal(df, snapshot)


def test_write_report_bytes_exactly(df, tmp_path):
    out = tmp_path / "report.csv"
    write_report(monthly_matrix(df), out)
    assert out.read_bytes() == EXPECTED_REPORT.encode("utf-8")


def test_write_report_formats_any_matrix_to_two_decimals(tmp_path):
    matrix = pd.DataFrame(
        [[1.5, 0.0], [2.345, 10.0]],
        index=pd.Index(["2025-04", "2025-05"], name="month"),
        columns=["alpha", "beta"],
    )
    out = tmp_path / "mini.csv"
    write_report(matrix, out)
    assert out.read_text() == (
        "month,alpha,beta\n"
        "2025-04,1.50,0.00\n"
        "2025-05,2.35,10.00\n"
    )
