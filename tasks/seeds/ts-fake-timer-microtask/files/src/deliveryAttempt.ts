export type TimerCallback = () => void | Promise<void>;

export interface Scheduler {
  now(): number;
  setTimeout(callback: TimerCallback, delayMs: number): unknown;
}

export interface Transport {
  send(payload: string): Promise<string>;
}

export type DeliverySnapshot =
  | { readonly phase: "idle" }
  | {
      readonly phase: "scheduled";
      readonly payload: string;
      readonly scheduledFor: number;
    }
  | {
      readonly phase: "sending";
      readonly payload: string;
      readonly startedAt: number;
    }
  | {
      readonly phase: "delivered";
      readonly payload: string;
      readonly startedAt: number;
      readonly finishedAt: number;
      readonly receipt: string;
    }
  | {
      readonly phase: "failed";
      readonly payload: string;
      readonly startedAt: number;
      readonly finishedAt: number;
      readonly error: string;
    };

export class DeliveryAttempt {
  #snapshot: DeliverySnapshot = { phase: "idle" };
  readonly #scheduler: Scheduler;
  readonly #transport: Transport;

  constructor(scheduler: Scheduler, transport: Transport) {
    this.#scheduler = scheduler;
    this.#transport = transport;
  }

  start(payload: string, delayMs: number): void {
    if (this.#snapshot.phase !== "idle") {
      throw new Error("delivery already started");
    }
    if (!Number.isFinite(delayMs) || delayMs < 0) {
      throw new RangeError("delayMs must be a non-negative finite number");
    }

    const scheduledFor = this.#scheduler.now() + delayMs;
    this.#snapshot = { phase: "scheduled", payload, scheduledFor };

    this.#scheduler.setTimeout(() => {
      // The host timer deliberately ignores asynchronous callback results.
      void this.#deliver(payload);
    }, delayMs);
  }

  snapshot(): DeliverySnapshot {
    return { ...this.#snapshot };
  }

  async #deliver(payload: string): Promise<void> {
    const startedAt = this.#scheduler.now();
    this.#snapshot = { phase: "sending", payload, startedAt };

    try {
      const receipt = await this.#transport.send(payload);
      this.#snapshot = {
        phase: "delivered",
        payload,
        startedAt,
        finishedAt: this.#scheduler.now(),
        receipt,
      };
    } catch (error) {
      this.#snapshot = {
        phase: "failed",
        payload,
        startedAt,
        finishedAt: this.#scheduler.now(),
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }
}
