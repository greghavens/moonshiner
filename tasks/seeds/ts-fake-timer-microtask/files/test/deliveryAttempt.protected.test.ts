import assert from "node:assert/strict";
import test from "node:test";

import {
  DeliveryAttempt,
  type Scheduler,
  type TimerCallback,
  type Transport,
} from "../src/deliveryAttempt.ts";

interface PendingTimer {
  readonly id: number;
  readonly dueAt: number;
  readonly callback: TimerCallback;
}

class ManualClock implements Scheduler {
  #now: number;
  #nextId = 1;
  readonly #timers = new Map<number, PendingTimer>();
  readonly scheduledDelays: number[] = [];

  constructor(now: number) {
    this.#now = now;
  }

  now(): number {
    return this.#now;
  }

  setTimeout(callback: TimerCallback, delayMs: number): number {
    const id = this.#nextId++;
    this.scheduledDelays.push(delayMs);
    this.#timers.set(id, { id, dueAt: this.#now + delayMs, callback });
    return id;
  }

  pendingCount(): number {
    return this.#timers.size;
  }

  async runAllAsync(): Promise<void> {
    while (this.#timers.size > 0) {
      const next = [...this.#timers.values()].sort(
        (left, right) => left.dueAt - right.dueAt || left.id - right.id,
      )[0];
      this.#timers.delete(next.id);
      this.#now = next.dueAt;
      await next.callback();
    }
  }
}

type Outcome =
  | { readonly kind: "resolve"; readonly receipt: string }
  | { readonly kind: "reject"; readonly error: Error };

class MicrotaskTransport implements Transport {
  readonly payloads: string[] = [];
  readonly #outcome: Outcome;

  constructor(outcome: Outcome) {
    this.#outcome = outcome;
  }

  send(payload: string): Promise<string> {
    this.payloads.push(payload);

    // Two queued turns make an early fake-timer assertion reproducible without
    // relying on wall-clock timing or sleeps.
    return new Promise((resolve, reject) => {
      queueMicrotask(() => {
        queueMicrotask(() => {
          if (this.#outcome.kind === "resolve") {
            resolve(this.#outcome.receipt);
          } else {
            reject(this.#outcome.error);
          }
        });
      });
    });
  }
}

test("runAllAsync reaches the exact delivered state", async () => {
  const clock = new ManualClock(4_000);
  const transport = new MicrotaskTransport({
    kind: "resolve",
    receipt: "receipt-17",
  });
  const attempt = new DeliveryAttempt(clock, transport);

  attempt.start("invoice-17", 25);

  assert.deepEqual(attempt.snapshot(), {
    phase: "scheduled",
    payload: "invoice-17",
    scheduledFor: 4_025,
  });
  assert.equal(clock.pendingCount(), 1);
  assert.deepEqual(clock.scheduledDelays, [25]);
  assert.deepEqual(transport.payloads, []);

  await clock.runAllAsync();

  assert.equal(clock.pendingCount(), 0);
  assert.deepEqual(clock.scheduledDelays, [25]);
  assert.deepEqual(transport.payloads, ["invoice-17"]);
  assert.deepEqual(attempt.snapshot(), {
    phase: "delivered",
    payload: "invoice-17",
    startedAt: 4_025,
    finishedAt: 4_025,
    receipt: "receipt-17",
  });
});

test("runAllAsync reaches the exact failed state", async () => {
  const clock = new ManualClock(8_100);
  const transport = new MicrotaskTransport({
    kind: "reject",
    error: new Error("gateway unavailable"),
  });
  const attempt = new DeliveryAttempt(clock, transport);

  attempt.start("invoice-23", 40);
  await clock.runAllAsync();

  assert.equal(clock.pendingCount(), 0);
  assert.deepEqual(clock.scheduledDelays, [40]);
  assert.deepEqual(transport.payloads, ["invoice-23"]);
  assert.deepEqual(attempt.snapshot(), {
    phase: "failed",
    payload: "invoice-23",
    startedAt: 8_140,
    finishedAt: 8_140,
    error: "gateway unavailable",
  });
});
