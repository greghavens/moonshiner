import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  InMemoryQueue,
  InMemorySpanRecorder,
  QueueWorker,
  type Clock,
} from "../src/queue-worker.ts";

class ManualClock implements Clock {
  #nowMs: number;

  constructor(nowMs: number) {
    this.#nowMs = nowMs;
  }

  now(): number {
    return this.#nowMs;
  }

  set(nowMs: number): void {
    this.#nowMs = nowMs;
  }
}

class AuditedQueue<T> extends InMemoryQueue<T> {
  readonly acknowledgements: string[] = [];
  readonly releases: string[] = [];

  override acknowledge(receipt: string): boolean {
    this.acknowledgements.push(receipt);
    return super.acknowledge(receipt);
  }

  override release(receipt: string): boolean {
    this.releases.push(receipt);
    return super.release(receipt);
  }
}

function deferred(): {
  promise: Promise<void>;
  resolve: () => void;
  reject: (error: Error) => void;
} {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function readJsonLines(relativePath: string): Record<string, unknown>[] {
  const contents = readFileSync(new URL(relativePath, import.meta.url), "utf8");
  return contents
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as Record<string, unknown>);
}

test("incident metrics and spans expose acknowledgement at handler start", () => {
  const metrics = readJsonLines("../incident/queue-metrics.jsonl");
  const spans = readJsonLines("../incident/worker-spans.jsonl");

  assert.deepEqual(
    metrics.map((sample) => sample.backlog),
    [2, 1, 1, 2],
  );
  assert.deepEqual(
    metrics.map((sample) => sample.oldestEnqueueAgeMs),
    [1_800, 9_400, 17_800, 26_700],
  );
  assert.ok(
    spans.every(
      (span) => span.acknowledgedAtMs === span.handlerStartedAtMs,
    ),
  );
  assert.ok(
    spans.every(
      (span) =>
        (span.handlerFinishedAtMs as number) >
        (span.acknowledgedAtMs as number),
    ),
  );

  const failed = spans.find((span) => span.outcome === "failed");
  assert.equal(failed?.jobId, "job-42");
  assert.equal(
    spans.filter((span) => span.jobId === failed?.jobId).length,
    1,
    "the acknowledged failure never received a retry attempt",
  );
});

test("pending handler work remains in backlog telemetry until success", async () => {
  const clock = new ManualClock(5_000);
  const queue = new AuditedQueue<string>();
  const spans = new InMemorySpanRecorder();
  const gate = deferred();
  queue.enqueue({ id: "oldest", payload: "first", enqueuedAtMs: 1_000 });
  queue.enqueue({ id: "next", payload: "second", enqueuedAtMs: 2_000 });

  const worker = new QueueWorker(queue, clock, spans, async () => gate.promise);
  const running = worker.runOne();
  await Promise.resolve();

  assert.deepEqual(queue.acknowledgements, []);
  assert.deepEqual(queue.releases, []);
  assert.deepEqual(worker.backlog(), {
    ready: 1,
    inFlight: 1,
    backlog: 2,
    oldestAgeMs: 4_000,
  });
  assert.deepEqual(spans.started(), [
    {
      jobId: "oldest",
      attempt: 1,
      startedAtMs: 5_000,
      queueLagMs: 4_000,
      backlogAtStart: {
        ready: 1,
        inFlight: 1,
        backlog: 2,
        oldestAgeMs: 4_000,
      },
    },
  ]);
  assert.deepEqual(spans.completed(), []);

  clock.set(6_500);
  gate.resolve();
  assert.deepEqual(await running, {
    outcome: "succeeded",
    jobId: "oldest",
    attempt: 1,
    backlog: {
      ready: 1,
      inFlight: 0,
      backlog: 1,
      oldestAgeMs: 4_500,
    },
  });
  assert.equal(queue.acknowledgements.length, 1);
  assert.deepEqual(queue.releases, []);
  assert.deepEqual(spans.completed()[0], {
    jobId: "oldest",
    attempt: 1,
    startedAtMs: 5_000,
    queueLagMs: 4_000,
    backlogAtStart: {
      ready: 1,
      inFlight: 1,
      backlog: 2,
      oldestAgeMs: 4_000,
    },
    finishedAtMs: 6_500,
    outcome: "succeeded",
    backlogAtEnd: {
      ready: 1,
      inFlight: 0,
      backlog: 1,
      oldestAgeMs: 4_500,
    },
  });
});

test("a rejected handler is released and succeeds on a later attempt", async () => {
  const clock = new ManualClock(10_000);
  const queue = new AuditedQueue<{ invoiceId: string }>();
  const spans = new InMemorySpanRecorder();
  const attempts: number[] = [];
  queue.enqueue({
    id: "charge-7",
    payload: { invoiceId: "invoice-7" },
    enqueuedAtMs: 8_000,
  });

  const worker = new QueueWorker(queue, clock, spans, async (delivery) => {
    attempts.push(delivery.attempt);
    if (delivery.attempt === 1) {
      clock.set(10_250);
      throw new Error("payments unavailable");
    }
    clock.set(10_500);
  });

  assert.deepEqual(await worker.runOne(), {
    outcome: "failed",
    jobId: "charge-7",
    attempt: 1,
    error: "payments unavailable",
    backlog: {
      ready: 1,
      inFlight: 0,
      backlog: 1,
      oldestAgeMs: 2_250,
    },
  });
  assert.deepEqual(worker.backlog(), {
    ready: 1,
    inFlight: 0,
    backlog: 1,
    oldestAgeMs: 2_250,
  });
  assert.deepEqual(queue.acknowledgements, []);
  assert.equal(queue.releases.length, 1);

  assert.deepEqual(await worker.runOne(), {
    outcome: "succeeded",
    jobId: "charge-7",
    attempt: 2,
    backlog: { ready: 0, inFlight: 0, backlog: 0, oldestAgeMs: 0 },
  });
  assert.equal(queue.acknowledgements.length, 1);
  assert.equal(queue.releases.length, 1);
  assert.deepEqual(attempts, [1, 2]);
  assert.deepEqual(
    spans.completed().map((span) => ({
      attempt: span.attempt,
      outcome: span.outcome,
      error: span.error,
      backlogAtEnd: span.backlogAtEnd,
    })),
    [
      {
        attempt: 1,
        outcome: "failed",
        error: "payments unavailable",
        backlogAtEnd: {
          ready: 1,
          inFlight: 0,
          backlog: 1,
          oldestAgeMs: 2_250,
        },
      },
      {
        attempt: 2,
        outcome: "succeeded",
        error: undefined,
        backlogAtEnd: {
          ready: 0,
          inFlight: 0,
          backlog: 0,
          oldestAgeMs: 0,
        },
      },
    ],
  );
});

test("an idle poll leaves telemetry and spans unchanged", async () => {
  const clock = new ManualClock(12_000);
  const queue = new InMemoryQueue<string>();
  const spans = new InMemorySpanRecorder();
  const worker = new QueueWorker(queue, clock, spans, async () => {
    throw new Error("handler must not run");
  });

  assert.deepEqual(await worker.runOne(), {
    outcome: "idle",
    backlog: { ready: 0, inFlight: 0, backlog: 0, oldestAgeMs: 0 },
  });
  assert.deepEqual(spans.started(), []);
  assert.deepEqual(spans.completed(), []);
});
