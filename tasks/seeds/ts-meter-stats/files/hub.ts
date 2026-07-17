export type Reading = {
  meterId: string;
  watts: number;
  at: number; // epoch ms the sample was taken
};

export type Handler = (reading: Reading) => void;

/**
 * Tiny in-process pub/sub used by the monitor daemon. Handlers are isolated:
 * one subscriber blowing up must never stop delivery to the rest, so failures
 * are swallowed and tallied instead of thrown.
 */
export class MeterHub {
  private handlers = new Map<string, Handler[]>();
  private failed = 0;

  subscribe(topic: string, handler: Handler): void {
    const list = this.handlers.get(topic) ?? [];
    list.push(handler);
    this.handlers.set(topic, list);
  }

  publish(topic: string, reading: Reading): number {
    let delivered = 0;
    for (const handler of this.handlers.get(topic) ?? []) {
      try {
        handler(reading);
        delivered++;
      } catch {
        this.failed++;
      }
    }
    return delivered;
  }

  subscriberCount(topic: string): number {
    return (this.handlers.get(topic) ?? []).length;
  }

  deliveryFailures(): number {
    return this.failed;
  }
}
