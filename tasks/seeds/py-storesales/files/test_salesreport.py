"""Acceptance tests for the regional sales rollup (salesreport.py).

Money is integer cents end to end, so every expected value here is exact.
The canonical region/category orderings are business orderings (north to
south, walk-order of the store aisles) — not alphabetical — and they are
part of the report contract, including rows for combinations with no sales.
"""
from pathlib import Path

import pandas as pd
import pytest
from pandas.api.types import CategoricalDtype

import salesreport


DATA = Path(__file__).parent / "data"

REGION_DTYPE = CategoricalDtype(["north", "central", "south"], ordered=True)
CATEGORY_DTYPE = CategoricalDtype(
    ["kitchen", "bath", "garden", "seasonal"], ordered=True)

EXPECTED_REPORT_CSV = """\
region,category,units,revenue_cents
north,kitchen,8,8500
north,bath,2,3798
north,garden,0,0
north,seasonal,3,6597
central,kitchen,2,1500
central,bath,4,7596
central,garden,5,6250
central,seasonal,0,0
south,kitchen,0,0
south,bath,1,1899
south,garden,2,2500
south,seasonal,0,0
"""


def make_tables():
    """Small in-memory tables mirroring load_tables' documented dtypes."""
    stores = pd.DataFrame({
        "store_id": pd.Series([1, 2], dtype="int64"),
        "city": ["Duluth", "Ames"],
        "region": pd.Series(["north", "central"], dtype=REGION_DTYPE),
    })
    products = pd.DataFrame({
        "sku": ["K100", "B100"],
        "product": ["steel whisk", "bath caddy"],
        "category": pd.Series(["kitchen", "bath"], dtype=CATEGORY_DTYPE),
        "unit_cents": pd.Series([750, 1899], dtype="int64"),
    })
    sales = pd.DataFrame({
        "store_id": pd.Series([1, 2, 1], dtype="int64"),
        "sku": ["K100", "B100", "B100"],
        "qty": pd.Series([4, 1, 2], dtype="int64"),
    })
    return {"stores": stores, "products": products, "sales": sales}


# ------------------------------------------------------------------ constants

def test_canonical_orderings_are_module_constants():
    assert salesreport.REGIONS == ["north", "central", "south"]
    assert salesreport.CATEGORIES == ["kitchen", "bath", "garden", "seasonal"]


# ---------------------------------------------------------------- load_tables

def test_load_tables_returns_all_three_tables():
    tables = salesreport.load_tables(DATA)
    assert set(tables) == {"stores", "products", "sales"}
    assert len(tables["stores"]) == 4
    assert len(tables["products"]) == 5
    assert len(tables["sales"]) == 10


def test_load_tables_region_is_ordered_categorical():
    tables = salesreport.load_tables(DATA)
    assert tables["stores"]["region"].dtype == REGION_DTYPE


def test_load_tables_category_is_ordered_categorical():
    tables = salesreport.load_tables(DATA)
    assert tables["products"]["category"].dtype == CATEGORY_DTYPE


def test_load_tables_integer_columns_are_int64():
    tables = salesreport.load_tables(DATA)
    assert tables["stores"]["store_id"].dtype == "int64"
    assert tables["products"]["unit_cents"].dtype == "int64"
    assert tables["sales"]["store_id"].dtype == "int64"
    assert tables["sales"]["qty"].dtype == "int64"


def test_load_tables_accepts_str_path():
    tables = salesreport.load_tables(str(DATA))
    assert len(tables["sales"]) == 10


# ----------------------------------------------------------------- build_fact

def test_build_fact_one_row_per_sale_line():
    fact = salesreport.build_fact(make_tables())
    assert len(fact) == 3


def test_build_fact_line_cents_is_qty_times_unit_cents():
    fact = salesreport.build_fact(make_tables())
    assert fact["line_cents"].dtype == "int64"
    by_key = {(r.store_id, r.sku): r.line_cents for r in fact.itertuples()}
    assert by_key[(1, "K100")] == 3000
    assert by_key[(2, "B100")] == 1899
    assert by_key[(1, "B100")] == 3798


def test_build_fact_keeps_categorical_dtypes():
    fact = salesreport.build_fact(make_tables())
    assert fact["region"].dtype == REGION_DTYPE
    assert fact["category"].dtype == CATEGORY_DTYPE


def test_build_fact_rejects_unknown_store_ids_sorted():
    tables = make_tables()
    tables["sales"] = pd.DataFrame({
        "store_id": pd.Series([9, 1, 7], dtype="int64"),
        "sku": ["K100", "K100", "B100"],
        "qty": pd.Series([1, 1, 1], dtype="int64"),
    })
    with pytest.raises(ValueError) as exc:
        salesreport.build_fact(tables)
    msg = str(exc.value)
    assert "7" in msg and "9" in msg
    assert msg.index("7") < msg.index("9")


def test_build_fact_rejects_unknown_skus_sorted():
    tables = make_tables()
    tables["sales"] = pd.DataFrame({
        "store_id": pd.Series([1, 1, 2], dtype="int64"),
        "sku": ["Z9", "A1", "K100"],
        "qty": pd.Series([1, 1, 1], dtype="int64"),
    })
    with pytest.raises(ValueError) as exc:
        salesreport.build_fact(tables)
    msg = str(exc.value)
    assert "A1" in msg and "Z9" in msg
    assert msg.index("A1") < msg.index("Z9")


def test_build_fact_rejects_duplicate_product_rows():
    tables = make_tables()
    dup = tables["products"].iloc[[0]]
    tables["products"] = pd.concat(
        [tables["products"], dup], ignore_index=True)
    with pytest.raises(ValueError):
        salesreport.build_fact(tables)


def test_build_fact_rejects_duplicate_store_rows():
    tables = make_tables()
    dup = tables["stores"].iloc[[0]]
    tables["stores"] = pd.concat([tables["stores"], dup], ignore_index=True)
    with pytest.raises(ValueError):
        salesreport.build_fact(tables)


# -------------------------------------------------------------- weekly_report

def full_report():
    return salesreport.weekly_report(
        salesreport.build_fact(salesreport.load_tables(DATA)))


def test_weekly_report_has_every_region_category_pair():
    rep = full_report()
    assert list(rep.columns) == ["region", "category", "units", "revenue_cents"]
    assert len(rep) == 12
    pairs = list(zip(rep["region"].tolist(), rep["category"].tolist()))
    assert pairs == [
        ("north", "kitchen"), ("north", "bath"),
        ("north", "garden"), ("north", "seasonal"),
        ("central", "kitchen"), ("central", "bath"),
        ("central", "garden"), ("central", "seasonal"),
        ("south", "kitchen"), ("south", "bath"),
        ("south", "garden"), ("south", "seasonal"),
    ]


def test_weekly_report_sums_are_exact():
    rep = full_report()
    assert rep["units"].dtype == "int64"
    assert rep["revenue_cents"].dtype == "int64"
    assert rep["units"].tolist() == [8, 2, 0, 3, 2, 4, 5, 0, 0, 1, 2, 0]
    assert rep["revenue_cents"].tolist() == [
        8500, 3798, 0, 6597, 1500, 7596, 6250, 0, 0, 1899, 2500, 0]


def test_weekly_report_zero_rows_for_combos_with_no_sales():
    rep = full_report()
    row = rep[(rep["region"] == "south") & (rep["category"] == "kitchen")]
    assert len(row) == 1
    assert row["units"].iloc[0] == 0
    assert row["revenue_cents"].iloc[0] == 0


# --------------------------------------------------------------- write_report

def test_write_report_csv_bytes_are_stable(tmp_path):
    out = tmp_path / "report.csv"
    salesreport.write_report(full_report(), out)
    assert out.read_text() == EXPECTED_REPORT_CSV


def test_write_report_accepts_str_path(tmp_path):
    out = tmp_path / "r2.csv"
    salesreport.write_report(full_report(), str(out))
    assert out.read_text() == EXPECTED_REPORT_CSV
