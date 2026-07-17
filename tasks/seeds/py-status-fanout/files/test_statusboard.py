"""Behavior checks for the status board. Run: python3 test_statusboard.py"""
import asyncio

from statusboard import ProbeError, StatusBoard

SERVICES = ["api", "db", "cache", "queue"]
LATENCIES = {"api": 12, "db": 48, "cache": 3, "queue": 27}


def make_probe(down):
    async def probe(name):
        await asyncio.sleep(0)
        if name in down:
            raise ProbeError(name, "connection refused")
        return LATENCIES[name]
    return probe


async def scenario():
    # All services healthy.
    board_all_ok = StatusBoard(SERVICES, make_probe(down=set()))
    board = await board_all_ok.refresh()
    assert sorted(board) == sorted(SERVICES), board
    assert board["api"] == {"status": "ok", "latency_ms": 12}, board["api"]
    assert board_all_ok.healthy() == ["api", "cache", "db", "queue"]
    assert board_all_ok.broken() == []

    # Two services down at once — the exact situation the dashboard is for.
    sb = StatusBoard(SERVICES, make_probe(down={"db", "queue"}))
    board = await sb.refresh()
    assert sorted(board) == sorted(SERVICES), (
        f"every service must appear on the board, got {sorted(board)}")
    assert board["api"] == {"status": "ok", "latency_ms": 12}, board["api"]
    assert board["cache"] == {"status": "ok", "latency_ms": 3}, board["cache"]
    for name in ("db", "queue"):
        entry = board[name]
        assert entry["status"] == "error", f"{name}: {entry}"
        assert "connection refused" in entry["detail"], f"{name}: {entry}"
    assert sb.healthy() == ["api", "cache"]
    assert sb.broken() == ["db", "queue"]

    # Recovery on the next cycle.
    sb2 = StatusBoard(SERVICES, make_probe(down=set()))
    board = await sb2.refresh()
    assert sb2.broken() == [] and len(board) == 4


def main():
    asyncio.run(scenario())
    print("all checks passed")


if __name__ == "__main__":
    main()
