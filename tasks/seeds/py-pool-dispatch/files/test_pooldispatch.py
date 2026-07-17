"""Behavior checks for the job dispatcher. Run: python3 test_pooldispatch.py"""
from pooldispatch import ConnectionPool, Dispatcher


def test_clean_batch():
    pool = ConnectionPool(size=2)
    disp = Dispatcher(pool)
    disp.run_batch([
        ("vacuum", lambda conn: conn.execute("VACUUM ANALYZE orders")),
        ("reindex", lambda conn: conn.execute("REINDEX TABLE events")),
    ])
    assert sorted(disp.results) == ["reindex", "vacuum"], disp.results
    assert disp.errors == {}, disp.errors
    assert pool.idle_count() == pool.size


def test_failed_batch_then_next_batch():
    pool = ConnectionPool(size=2)
    disp = Dispatcher(pool)

    def locked(conn):
        raise RuntimeError("stats table locked by replication")

    # Tonight's batch: both jobs hit the replication lock and fail.
    disp.run_batch([("stats-daily", locked), ("stats-weekly", locked)])
    assert sorted(disp.errors) == ["stats-daily", "stats-weekly"], disp.errors
    assert "stats table locked" in disp.errors["stats-daily"]
    assert disp.results == {}, disp.results

    # Tomorrow's batch on the same pool: an ordinary job must still run.
    disp.run_batch([("vacuum", lambda conn: conn.execute("VACUUM ANALYZE orders"))])
    assert "vacuum" in disp.results, (
        f"job after a failed batch starved; errors={disp.errors}")
    assert "vacuum" not in disp.errors, disp.errors["vacuum"]
    assert pool.idle_count() == pool.size, (
        f"{pool.idle_count()} of {pool.size} sessions idle after all batches finished")


def main():
    test_clean_batch()
    test_failed_batch_then_next_batch()
    print("all checks passed")


if __name__ == "__main__":
    main()
