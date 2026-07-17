"""Behavior checks for the telemetry buffer. Run: python3 test_telemetry.py"""
import asyncio

from telemetry import TelemetryBuffer


class RecordingSink:
    """Stands in for the HTTP ingest client; write() yields to the event
    loop mid-flight, exactly like a real network write does."""

    def __init__(self, on_write=None):
        self.batches = []
        self._on_write = on_write

    async def write(self, readings):
        self.batches.append(list(readings))
        if self._on_write is not None:
            await self._on_write()


async def scenario_simple():
    sink = RecordingSink()
    buf = TelemetryBuffer(sink)
    assert await buf.flush() == 0, "empty flush should upload nothing"
    assert sink.batches == []

    buf.add({"t": 1, "temp": 21.5})
    buf.add({"t": 2, "temp": 21.7})
    assert buf.pending_count() == 2
    assert await buf.flush() == 2
    assert sink.batches == [[{"t": 1, "temp": 21.5}, {"t": 2, "temp": 21.7}]]
    assert await buf.flush() == 0, "nothing new arrived, nothing to upload"
    assert buf.uploaded_count == 2


async def scenario_reading_arrives_mid_upload():
    gate = asyncio.Event()

    async def in_flight():
        gate.set()
        await asyncio.sleep(0)

    sink = RecordingSink(on_write=in_flight)
    buf = TelemetryBuffer(sink)

    async def sensor():
        await gate.wait()  # fires while the first upload is on the wire
        buf.add({"t": 3, "temp": 21.9})

    buf.add({"t": 1, "temp": 21.5})
    buf.add({"t": 2, "temp": 21.7})
    sensor_task = asyncio.create_task(sensor())

    n1 = await buf.flush()
    await sensor_task
    n2 = await buf.flush()

    uploaded = [r for batch in sink.batches for r in batch]
    got = sorted(r["t"] for r in uploaded)
    assert got == [1, 2, 3], f"ingest should receive every reading exactly once, got t={got}"
    assert n1 + n2 == 3, (n1, n2)
    assert buf.uploaded_count == 3, buf.uploaded_count
    assert buf.pending_count() == 0


def main():
    asyncio.run(scenario_simple())
    asyncio.run(scenario_reading_arrives_mid_upload())
    print("all checks passed")


if __name__ == "__main__":
    main()
