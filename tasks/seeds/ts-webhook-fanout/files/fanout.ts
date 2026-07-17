export type SendFn = (url: string, body: string) => Promise<void>;

export type FanoutReport = {
  delivered: string[];
  failed: { url: string; reason: string }[];
};

/**
 * Deliver one payload to every subscriber endpoint and report per-endpoint
 * outcomes. Endpoints are independent third parties, so deliveries run
 * concurrently. The report feeds the audit log and the retry queue.
 */
export async function fanout(
  urls: string[],
  body: string,
  send: SendFn,
): Promise<FanoutReport> {
  const delivered: string[] = [];
  const failed: { url: string; reason: string }[] = [];

  const deliveries = urls.map(async (url) => {
    await send(url, body);
    delivered.push(url);
  });

  try {
    await Promise.all(deliveries);
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    for (const url of urls) {
      if (!delivered.includes(url)) {
        failed.push({ url, reason });
      }
    }
  }

  return { delivered, failed };
}

/** Fraction of endpoints that got the payload, for the health gauge. */
export function deliveryRate(report: FanoutReport): number {
  const total = report.delivered.length + report.failed.length;
  return total === 0 ? 1 : report.delivered.length / total;
}
