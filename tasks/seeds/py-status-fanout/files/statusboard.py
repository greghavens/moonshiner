"""Status dashboard aggregator.

Every refresh cycle fans out one probe per registered service and
assembles the board the on-call dashboard renders. The board contract:
every registered service appears exactly once per refresh — healthy
services with their latency, broken ones with the error their probe
reported. One dead service must never hide the health (or sickness) of
the others; refresh() itself never raises.
"""
import asyncio


class ProbeError(RuntimeError):
    """A probe conclusively failed for one service."""

    def __init__(self, service, message):
        super().__init__(f"{service}: {message}")
        self.service = service
        self.message = message


class StatusBoard:
    def __init__(self, services, probe):
        self._services = list(services)
        self._probe = probe  # async callable(name) -> latency ms; raises ProbeError
        self.last_board = {}

    async def _check(self, name):
        latency = await self._probe(name)
        return name, latency

    async def refresh(self):
        """Probe all services concurrently and rebuild the board."""
        board = {}
        try:
            pairs = await asyncio.gather(
                *(self._check(name) for name in self._services))
        except ProbeError as exc:
            board[exc.service] = {"status": "error", "detail": exc.message}
            pairs = []
        for name, latency in pairs:
            board[name] = {"status": "ok", "latency_ms": latency}
        self.last_board = board
        return board

    def healthy(self):
        """Names of services currently reporting ok, sorted."""
        return sorted(n for n, e in self.last_board.items()
                      if e["status"] == "ok")

    def broken(self):
        """Names of services currently reporting an error, sorted."""
        return sorted(n for n, e in self.last_board.items()
                      if e["status"] == "error")
