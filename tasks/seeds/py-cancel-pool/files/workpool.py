"""A tiny asyncio worker pool with a fixed concurrency budget."""

import asyncio


class WorkerPool:
    """Runs submitted jobs with at most `capacity` running at once.

    submit() schedules an async job (a zero-arg coroutine function) and
    returns its task; shutdown() waits for all submitted work, then takes
    back every permit so nothing else can start.
    """

    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._permits = asyncio.Semaphore(capacity)
        self._tasks = set()

    def submit(self, fn):
        """Schedule fn() to run under the pool's concurrency budget."""
        task = asyncio.create_task(self._run(fn))
        self._tasks.add(task)
        return task

    async def _run(self, fn):
        await self._permits.acquire()
        result = await fn()
        self._permits.release()
        self._tasks.discard(asyncio.current_task())
        return result

    def pending(self):
        """Jobs submitted whose tasks have not finished."""
        return len(self._tasks)

    async def shutdown(self):
        """Wait for all submitted jobs, then drain the pool's permits."""
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        for _ in range(self.capacity):
            await self._permits.acquire()
