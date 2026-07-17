"""Behavior contract for the stock-feed batch importer.

Runs against a real temporary SQLite database file in the workspace.
Connections are opened in autocommit mode (isolation_level=None): the
importer owns all transaction boundaries itself.
"""

import os
import sqlite3

from stockimport import BatchError, ensure_schema, import_batch

DB = "stock_test.db"


def connect():
    return sqlite3.connect(DB, isolation_level=None)


def fresh_db():
    if os.path.exists(DB):
        os.remove(DB)
    conn = connect()
    ensure_schema(conn)
    return conn


def table(conn):
    return sorted(conn.execute("SELECT sku, qty, bin FROM stock").fetchall())


def row(sku, qty, bin_code):
    return {"sku": sku, "qty": qty, "bin": bin_code}


def test_clean_batch_all_inserted_and_durable():
    conn = fresh_db()
    report = import_batch(conn, [row("A1", 4, "B-01"), row("A2", 1, "B-02")])
    assert report == {"inserted": 2, "skipped": []}
    assert table(conn) == [("A1", 4, "B-01"), ("A2", 1, "B-02")]
    conn.close()
    conn2 = connect()
    assert table(conn2) == [("A1", 4, "B-01"), ("A2", 1, "B-02")]
    conn2.close()


def test_duplicate_skips_only_its_own_row():
    conn = fresh_db()
    import_batch(conn, [row("A1", 4, "B-01")])
    report = import_batch(
        conn,
        [row("B1", 2, "B-03"), row("A1", 9, "B-09"), row("B2", 5, "B-04")],
    )
    assert report == {"inserted": 2, "skipped": ["A1"]}
    assert table(conn) == [
        ("A1", 4, "B-01"),
        ("B1", 2, "B-03"),
        ("B2", 5, "B-04"),
    ], "rows before and after the skipped duplicate must both land; A1 stays untouched"
    conn.close()


def test_duplicate_within_batch_skipped():
    conn = fresh_db()
    report = import_batch(
        conn,
        [row("D1", 1, "B-01"), row("D1", 2, "B-02"), row("D2", 3, "B-03")],
    )
    assert report == {"inserted": 2, "skipped": ["D1"]}
    assert table(conn) == [("D1", 1, "B-01"), ("D2", 3, "B-03")]
    conn.close()


def test_contract_violation_rejects_whole_batch():
    conn = fresh_db()
    import_batch(conn, [row("A1", 4, "B-01")])
    caught = None
    try:
        import_batch(
            conn,
            [
                row("N1", 1, "B-10"),
                row("A1", 7, "B-11"),
                row("N2", 2, "B-12"),
                row("N3", -4, "B-13"),
            ],
        )
    except BatchError as err:
        caught = err
    assert caught is not None
    assert (caught.index, caught.sku, caught.reason) == (
        3,
        "N3",
        "qty must be a non-negative integer",
    )
    assert "row 3" in str(caught) and "N3" in str(caught)
    assert table(conn) == [("A1", 4, "B-01")], "no row of a rejected batch may persist"
    conn.close()
    conn2 = connect()
    assert table(conn2) == [("A1", 4, "B-01")], "rejected rows must not be durable either"
    conn2.close()


def test_violation_on_first_row():
    conn = fresh_db()
    caught = None
    try:
        import_batch(conn, [{"sku": "", "qty": 1, "bin": "B-01"}, row("Z1", 1, "B-02")])
    except BatchError as err:
        caught = err
    assert caught is not None
    assert (caught.index, caught.sku, caught.reason) == (0, "", "missing sku")
    assert table(conn) == []
    conn.close()


def test_bool_qty_rejected():
    conn = fresh_db()
    caught = None
    try:
        import_batch(conn, [row("Q1", True, "B-01")])
    except BatchError as err:
        caught = err
    assert caught is not None
    assert caught.reason == "qty must be a non-negative integer"
    assert table(conn) == []
    conn.close()


def test_connection_usable_after_rejected_batch():
    conn = fresh_db()
    try:
        import_batch(conn, [row("X1", 1, "B-01"), {"sku": "X2", "qty": 2, "bin": ""}])
    except BatchError:
        pass
    report = import_batch(conn, [row("Y1", 3, "B-02")])
    assert report == {"inserted": 1, "skipped": []}
    assert table(conn) == [("Y1", 3, "B-02")]
    conn.close()


def main():
    tests = [fn for name, fn in sorted(list(globals().items())) if name.startswith("test_")]
    try:
        for fn in tests:
            fn()
            print(f"ok {fn.__name__}")
    finally:
        if os.path.exists(DB):
            os.remove(DB)
    print(f"{len(tests)} checks passed")


if __name__ == "__main__":
    main()
