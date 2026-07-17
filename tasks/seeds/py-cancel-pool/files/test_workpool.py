import asyncio

from workpool import WorkerPool

TIMEOUT = 5


async def ticks(n=5):
    """Deterministic scheduler yields; no wall-clock waiting involved."""
    for _ in range(n):
        await asyncio.sleep(0)


async def scenario_cancel_while_running():
    pool = WorkerPool(1)
    started = asyncio.Event()
    hold = asyncio.Event()

    async def stuck():
        started.set()
        await hold.wait()
        return "stuck-done"

    first = pool.submit(stuck)
    await asyncio.wait_for(started.wait(), TIMEOUT)
    first.cancel()
    try:
        await first
        raise AssertionError("cancelled task should not return")
    except asyncio.CancelledError:
        pass

    ran = asyncio.Event()

    async def follow_up():
        ran.set()
        return "follow-up-done"

    second = pool.submit(follow_up)
    await asyncio.wait_for(ran.wait(), TIMEOUT)
    assert await asyncio.wait_for(second, TIMEOUT) == "follow-up-done"
    await asyncio.wait_for(pool.shutdown(), TIMEOUT)
    await ticks()
    assert pool.pending() == 0


async def scenario_cancel_before_permit():
    pool = WorkerPool(1)
    started = asyncio.Event()
    hold = asyncio.Event()

    async def holder():
        started.set()
        await hold.wait()
        return "held"

    first = pool.submit(holder)
    await asyncio.wait_for(started.wait(), TIMEOUT)

    async def queued():
        return "queued"

    waiting = pool.submit(queued)
    await ticks()  # let it reach the point of waiting for capacity
    waiting.cancel()
    try:
        await asyncio.wait_for(waiting, TIMEOUT)
        raise AssertionError("queued job should have been cancelled")
    except asyncio.CancelledError:
        pass

    hold.set()
    assert await asyncio.wait_for(first, TIMEOUT) == "held"

    # Capacity must still be exactly one: two probes may never overlap.
    active = 0
    peak = []
    gate = asyncio.Event()
    started_c = asyncio.Event()
    started_d = asyncio.Event()

    async def probe(started_evt):
        nonlocal active
        active += 1
        peak.append(active)
        started_evt.set()
        await gate.wait()
        active -= 1
        return "probe"

    c = pool.submit(lambda: probe(started_c))
    d = pool.submit(lambda: probe(started_d))
    await asyncio.wait_for(started_c.wait(), TIMEOUT)
    await ticks()
    assert not started_d.is_set(), "capacity-1 pool ran two jobs at once"
    gate.set()
    await asyncio.wait_for(started_d.wait(), TIMEOUT)
    assert await asyncio.wait_for(c, TIMEOUT) == "probe"
    assert await asyncio.wait_for(d, TIMEOUT) == "probe"
    assert max(peak) == 1
    await asyncio.wait_for(pool.shutdown(), TIMEOUT)
    await ticks()
    assert pool.pending() == 0


async def scenario_job_failure():
    pool = WorkerPool(2)

    async def explode():
        raise RuntimeError("bad payload")

    doomed = pool.submit(explode)
    try:
        await asyncio.wait_for(doomed, TIMEOUT)
        raise AssertionError("job exception should propagate")
    except RuntimeError as exc:
        assert str(exc) == "bad payload"

    # Both permits must survive the failure: two jobs can run together.
    started = [asyncio.Event(), asyncio.Event()]
    gate = asyncio.Event()

    async def pair(evt):
        evt.set()
        await gate.wait()
        return "pair"

    t0 = pool.submit(lambda: pair(started[0]))
    t1 = pool.submit(lambda: pair(started[1]))
    await asyncio.wait_for(started[0].wait(), TIMEOUT)
    await asyncio.wait_for(started[1].wait(), TIMEOUT)
    gate.set()
    assert await asyncio.wait_for(t0, TIMEOUT) == "pair"
    assert await asyncio.wait_for(t1, TIMEOUT) == "pair"
    await asyncio.wait_for(pool.shutdown(), TIMEOUT)
    await ticks()
    assert pool.pending() == 0


async def scenario_shutdown_drains():
    pool = WorkerPool(2)
    hold = asyncio.Event()
    started = asyncio.Event()

    async def ok():
        return 41

    async def slow():
        started.set()
        await hold.wait()
        return 42

    async def bad():
        raise ValueError("nope")

    a = pool.submit(ok)
    s = pool.submit(slow)
    await asyncio.wait_for(started.wait(), TIMEOUT)
    b = pool.submit(bad)
    victim = pool.submit(ok)
    victim.cancel()
    try:
        await asyncio.wait_for(victim, TIMEOUT)
    except asyncio.CancelledError:
        pass
    assert await asyncio.wait_for(a, TIMEOUT) == 41
    try:
        await asyncio.wait_for(b, TIMEOUT)
        raise AssertionError("bad job should raise")
    except ValueError:
        pass
    hold.set()
    assert await asyncio.wait_for(s, TIMEOUT) == 42
    await asyncio.wait_for(pool.shutdown(), TIMEOUT)
    await ticks()
    assert pool.pending() == 0


def main():
    for scenario in (
        scenario_cancel_while_running,
        scenario_cancel_before_permit,
        scenario_job_failure,
        scenario_shutdown_drains,
    ):
        asyncio.run(asyncio.wait_for(scenario(), TIMEOUT * 4))
    print("all workpool tests passed")


if __name__ == "__main__":
    main()
