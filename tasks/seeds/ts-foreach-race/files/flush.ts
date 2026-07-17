// Flushes buffered telemetry readings into the metrics store. The edge
// agent calls this right before rotating its on-disk buffer, so the report
// returned here decides which readings are safe to delete from disk.

export interface Reading {
  id: string;
  value: number;
}

export interface WriteStore {
  write(reading: Reading): Promise<{ id: string }>;
}

export interface FlushOptions {
  concurrency?: number;
}

export interface FlushReport {
  ok: string[];
  failed: { id: string; reason: string }[];
}

export async function flushReadings(
  store: WriteStore,
  readings: Reading[],
  options: FlushOptions = {},
): Promise<FlushReport> {
  const concurrency = Math.max(1, options.concurrency ?? 4);
  const ok: string[] = [];
  const failed: { id: string; reason: string }[] = [];

  const chunks: Reading[][] = [];
  for (let i = 0; i < readings.length; i += concurrency) {
    chunks.push(readings.slice(i, i + concurrency));
  }

  chunks.forEach(async (chunk) => {
    await Promise.all(
      chunk.map(async (reading) => {
        const receipt = await store.write(reading);
        ok.push(receipt.id);
      }),
    );
  });

  return { ok, failed };
}
