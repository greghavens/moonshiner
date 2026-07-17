"""Telemetry batching uploader for the greenhouse device agent (asyncio).

Sensor coroutines call add() as readings arrive; a periodic task calls
flush() to push everything collected so far to the ingest endpoint via
the injected sink (an HTTP client in production). Contract: every
reading handed to add() is uploaded exactly once — readings that arrive
while an upload is in flight simply belong to the next flush.
"""


class TelemetryBuffer:
    def __init__(self, sink):
        self._sink = sink
        self._pending = []
        self.uploaded_count = 0

    def add(self, reading):
        """Queue one reading for the next upload."""
        self._pending.append(reading)

    def pending_count(self):
        """Readings collected but not yet uploaded."""
        return len(self._pending)

    async def flush(self):
        """Upload everything collected so far; returns how many went out."""
        if not self._pending:
            return 0
        await self._sink.write(self._pending)
        count = len(self._pending)
        self._pending.clear()
        self.uploaded_count += count
        return count
